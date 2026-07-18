"""ACP backend: drives an Agent Client Protocol agent subprocess.

Speaks JSON-RPC 2.0 over newline-delimited stdio (hand-rolled: the client
surface we need is tiny, and tolerant dict parsing survives agents that emit
newer protocol variants). One backend instance = one subprocess = one ACP
session = one browser tab.

Trust model: ``session/request_permission`` is auto-allowed, mirroring the
claude-sdk backend's ``bypassPermissions`` — the agent may run arbitrary
commands. Acceptable for the localhost-bound chat UI; do not expose beyond it.
"""

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator

from artmind.webui.backends.acp_events import ACPEventMapper
from artmind.webui.backends.base import UIEvent
from artmind.webui.profiles import QA_PROFILE

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
CONNECT_TIMEOUT_S = 60.0

_TURN_END = object()


class ACPBackend:
    def __init__(
        self,
        agent_cmd: list[str],
        cwd: str,
        prompt_preamble: bool = False,
        mode: str | None = None,
        preamble_text: str | None = None,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
    ) -> None:
        self._agent_cmd = agent_cmd
        self._cwd = cwd
        self._prompt_preamble = prompt_preamble
        self._mode = mode
        # Profile persona to prepend when ``prompt_preamble`` is set; falls back
        # to the Q&A persona to preserve the pre-profile default behaviour.
        self._preamble_text = preamble_text or QA_PROFILE.system_append
        self._connect_timeout_s = connect_timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._read_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._turn_task: asyncio.Task | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue()
        self._write_lock = asyncio.Lock()
        self._session_id: str | None = None
        self._mapper: ACPEventMapper | None = None
        self._first_prompt_sent = False

    async def connect(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._agent_cmd,
            cwd=self._cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        if self._proc.stdout is not None:
            self._proc.stdout._limit = 10 * 1024 * 1024  # 10 MiB
        self._read_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        await self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False}
                },
                "clientInfo": {"name": "artmind-chat-ui", "version": "1.0"},
            },
            timeout_s=self._connect_timeout_s,
        )
        result = await self._request(
            "session/new",
            {"cwd": self._cwd, "mcpServers": []},
            timeout_s=self._connect_timeout_s,
        )
        self._session_id = result["sessionId"]
        if self._mode:
            # opencode surfaces custom agents (e.g. the artmind persona in
            # .opencode/agent/) as ACP session modes; other agents may not
            # know the mode id, so a failure is downgraded to a warning.
            try:
                await self._request(
                    "session/set_mode",
                    {"sessionId": self._session_id, "modeId": self._mode},
                    timeout_s=10.0,
                )
            except Exception as exc:
                logger.warning("ACP: could not set mode %r: %s", self._mode, exc)

    async def query(self, prompt: str) -> None:
        if self._prompt_preamble and not self._first_prompt_sent:
            prompt = f"{self._preamble_text}\n\n{prompt}"
        self._first_prompt_sent = True
        self._mapper = ACPEventMapper()
        started_at = time.monotonic()
        future = await self._send_request(
            "session/prompt",
            {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": prompt}],
            },
        )
        self._turn_task = asyncio.create_task(self._finish_turn(future, started_at))

    async def _finish_turn(self, future: asyncio.Future, started_at: float) -> None:
        mapper = self._mapper
        try:
            result = await future
            stop_reason = (result or {}).get("stopReason", "end_turn")
        except Exception as exc:
            self._events.put_nowait({"type": "error", "message": str(exc)})
            stop_reason = "error"
        if mapper is not None:
            for event in mapper.finish(stop_reason, time.monotonic() - started_at):
                self._events.put_nowait(event)
        self._events.put_nowait(_TURN_END)

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        while True:
            item = await self._events.get()
            if item is _TURN_END:
                return
            yield item

    async def interrupt(self) -> None:
        if self._session_id is not None:
            await self._notify("session/cancel", {"sessionId": self._session_id})

    async def disconnect(self) -> None:
        proc, self._proc = self._proc, None
        for task in (self._read_task, self._stderr_task, self._turn_task):
            if task is not None:
                task.cancel()
        self._fail_pending(RuntimeError("ACP backend disconnected"))
        if proc is None:
            return
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except TimeoutError:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 2)
            except TimeoutError:
                proc.kill()
                await proc.wait()

    # ---- JSON-RPC plumbing ------------------------------------------------

    async def _write(self, message: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("ACP agent is not running")
        data = (json.dumps(message) + "\n").encode()
        async with self._write_lock:
            proc.stdin.write(data)
            await proc.stdin.drain()

    async def _send_request(self, method: str, params: dict) -> asyncio.Future:
        self._next_id += 1
        message_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": message_id, "method": method, "params": params}
            )
        except BaseException:
            self._pending.pop(message_id, None)
            raise
        return future

    async def _request(self, method: str, params: dict, timeout_s: float) -> Any:
        future = await self._send_request(method, params)
        return await asyncio.wait_for(future, timeout_s)

    async def _notify(self, method: str, params: dict) -> None:
        await self._write({"jsonrpc": "2.0", "method": method, "params": params})

    async def _respond(
        self, message_id: Any, result: Any = None, error: dict | None = None
    ) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": message_id}
        if error is not None:
            message["error"] = error
        else:
            message["result"] = result
        await self._write(message)

    # ---- incoming traffic -------------------------------------------------

    async def _read_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            while True:
                try:
                    line = await proc.stdout.readline()
                except ValueError:
                    logger.warning("ACP: readline buffer overrun, draining oversized frame")
                    while True:
                        chunk = await proc.stdout.read(4096)
                        if not chunk or b"\n" in chunk:
                            break
                    continue
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("ACP: dropping malformed frame: %.200s", line)
                    continue
                try:
                    await self._dispatch(message)
                except Exception:
                    logger.exception("ACP: failed dispatching %.200s", line)
        finally:
            code = proc.returncode
            self._fail_pending(RuntimeError(f"ACP agent exited (code {code})"))

    def _fail_pending(self, exc: Exception) -> None:
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(exc)

    async def _dispatch(self, message: dict[str, Any]) -> None:
        if "result" in message or "error" in message:
            future = self._pending.pop(message.get("id"), None)
            if future is None or future.done():
                return
            if "error" in message:
                err = message["error"] or {}
                future.set_exception(
                    RuntimeError(err.get("message", "ACP agent error"))
                )
            else:
                future.set_result(message.get("result"))
            return

        method = message.get("method")
        params = message.get("params") or {}
        if method == "session/update":
            if self._mapper is not None:
                for event in self._mapper.map_update(params.get("update") or {}):
                    self._events.put_nowait(event)
        elif method == "session/request_permission":
            await self._respond(
                message["id"], {"outcome": self._permission_outcome(params)}
            )
        elif "id" in message:
            # A request we don't serve (we advertise no fs/terminal caps).
            await self._respond(
                message["id"],
                error={"code": -32601, "message": f"method not supported: {method}"},
            )
        # else: unknown notification, ignore

    @staticmethod
    def _permission_outcome(params: dict[str, Any]) -> dict[str, Any]:
        options = params.get("options") or []
        chosen = None
        for kind in ("allow_always", "allow_once"):
            chosen = next((o for o in options if o.get("kind") == kind), None)
            if chosen:
                break
        chosen = chosen or (options[0] if options else None)
        if chosen is None:
            return {"outcome": "cancelled"}
        title = (params.get("toolCall") or {}).get("title", "?")
        logger.info(
            "ACP: auto-allowing permission for %s (%s)", title, chosen.get("kind")
        )
        return {"outcome": "selected", "optionId": chosen.get("optionId")}

    async def _drain_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                return
            logger.debug("ACP agent stderr: %s", line.decode(errors="replace").rstrip())
