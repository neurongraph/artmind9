"""Batch Q&A benchmark driver: parse a question file, run each question through
the artmind query agent one after another, and record every answer.

Parsing and CRUD here are dependency-free (stdlib + artmind.db only) so they
stay importable and testable without the Claude Agent SDK / FastAPI. The
actual agent-driving loop (``_run_benchmark``) lazily imports
``artmind.webui.backends``/``artmind.webui.profiles`` inside the function body,
mirroring how ``artmind/cli.py`` lazily imports ``artmind.webui`` for the
``admin-ui``/``chat-ui`` commands rather than pulling those deps in at
module load time.
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from artmind.db import _get_db

# ── parsing ──────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)
_QID_RE = re.compile(r"\bQ(\d+)\b", re.IGNORECASE)
_TITLE_QID_PREFIX_RE = re.compile(r"^Q\d+\s*[—\-:]\s*", re.IGNORECASE)
_QUESTION_LABEL_RE = re.compile(r"\*\*Question:?\*\*\s*", re.IGNORECASE)
_EVAL_LABEL_RE = re.compile(r"\*\*Evaluation comment:?\*\*\s*", re.IGNORECASE)


def _split_headings(text: str) -> list[tuple[str, str]]:
    """Split ``text`` into ``(heading_title, block_body)`` pairs at every
    markdown heading line, in document order. Empty if there are no headings."""
    matches = list(_HEADING_RE.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        title = m.group(2).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append((title, text[start:end].strip()))
    return blocks


def _extract_labelled(body: str, label_re: re.Pattern, stop_res: list[re.Pattern]) -> str | None:
    m = label_re.search(body)
    if not m:
        return None
    end = len(body)
    for stop_re in stop_res:
        sm = stop_re.search(body, m.end())
        if sm and sm.start() < end:
            end = sm.start()
    text = body[m.end():end].strip()
    return text or None


def _qid_from_title(title: str | None, seq: int) -> str:
    m = _QID_RE.search(title or "")
    return f"Q{int(m.group(1)):02d}" if m else f"Q{seq:02d}"


def _clean_title(title: str | None) -> str | None:
    if not title:
        return title
    return _TITLE_QID_PREFIX_RE.sub("", title).strip() or title


def _parse_questions(text: str) -> list[dict]:
    """Parse a semi-structured markdown Q&A file into question records.

    Tries progressively looser strategies so files that don't match the
    reference corpus's exact heading/label style still parse into something
    usable:

    1. Heading blocks containing a ``**Question:**`` label (+ optional
       ``**Evaluation comment:**`` label) — the reference corpus's shape.
    2. No heading match, but ``**Question:**`` labels exist somewhere in the
       text — split directly on those labels.
    3. No ``**Question:**`` labels at all — one question per heading block,
       verbatim.
    4. No headings either — one question per non-empty line ending in ``?``.

    Each row: ``seq`` (1-based position), ``qid`` (from a "Q07"-shaped
    heading if present, else ``Q{seq:02d}``), ``title``, ``question``, and
    ``eval_comment`` (nullable — never sent to the agent; grading notes only).
    """
    text = text.replace("\r\n", "\n")
    blocks = _split_headings(text)

    # Strategy 1: heading blocks with an explicit **Question:** label.
    if blocks:
        rows = []
        seq = 0
        for title, body in blocks:
            question = _extract_labelled(body, _QUESTION_LABEL_RE, [_EVAL_LABEL_RE])
            if question is None:
                continue
            seq += 1
            rows.append({
                "seq": seq,
                "qid": _qid_from_title(title, seq),
                "title": _clean_title(title),
                "question": question,
                "eval_comment": _extract_labelled(body, _EVAL_LABEL_RE, []),
            })
        if rows:
            return rows

    # Strategy 2: **Question:** labels exist but weren't heading-scoped.
    label_matches = list(_QUESTION_LABEL_RE.finditer(text))
    if label_matches:
        rows = []
        for i, m in enumerate(label_matches):
            end = label_matches[i + 1].start() if i + 1 < len(label_matches) else len(text)
            chunk = text[m.end():end]
            eval_m = _EVAL_LABEL_RE.search(chunk)
            question = (chunk[:eval_m.start()] if eval_m else chunk).strip()
            if not question:
                continue
            seq = len(rows) + 1
            rows.append({
                "seq": seq,
                "qid": f"Q{seq:02d}",
                "title": None,
                "question": question,
                "eval_comment": _extract_labelled(chunk, _EVAL_LABEL_RE, []),
            })
        if rows:
            return rows

    # Strategy 3: no **Question:** labels — one question per heading block, verbatim.
    if blocks:
        rows = []
        for title, body in blocks:
            body = body.strip()
            if not body:
                continue
            seq = len(rows) + 1
            rows.append({
                "seq": seq,
                "qid": _qid_from_title(title, seq),
                "title": _clean_title(title),
                "question": body,
                "eval_comment": None,
            })
        if rows:
            return rows

    # Strategy 4: last resort — one question per non-empty line ending in '?'.
    rows = []
    for line in text.split("\n"):
        line = line.strip().lstrip("-*0123456789. ").strip()
        if line.endswith("?"):
            seq = len(rows) + 1
            rows.append({
                "seq": seq,
                "qid": f"Q{seq:02d}",
                "title": None,
                "question": line,
                "eval_comment": None,
            })
    return rows


def _parse_questions_file(path: str | Path) -> list[dict]:
    return _parse_questions(Path(path).read_text(encoding="utf-8"))


# ── run CRUD ─────────────────────────────────────────────────────────────

def _create_benchmark_run(
    source_path: str,
    questions: list[dict],
    domain_hint: str | None = None,
    backend: str = "claude-sdk",
    name: str | None = None,
) -> str:
    """Create a new benchmark run with per-question rows; return run_id."""
    run_id = str(uuid.uuid4())
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO benchmark_runs"
            " (run_id, name, domain_hint, source_path, backend, status, question_count, queued_at)"
            " VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)",
            (run_id, name, domain_hint, source_path, backend, len(questions), datetime.now().isoformat()),
        )
        conn.executemany(
            "INSERT INTO benchmark_questions"
            " (run_id, seq, qid, title, question, eval_comment, status)"
            " VALUES (?, ?, ?, ?, ?, ?, 'queued')",
            [
                (run_id, q["seq"], q["qid"], q["title"], q["question"], q["eval_comment"])
                for q in questions
            ],
        )
        conn.commit()
        return run_id
    finally:
        conn.close()


def _update_benchmark_run(
    run_id: str,
    status: str | None = None,
    processed_count: int | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    conn = _get_db()
    try:
        updates, params = [], []
        for col, val in (
            ("status", status),
            ("processed_count", processed_count),
            ("started_at", started_at),
            ("completed_at", completed_at),
            ("error_message", error_message),
        ):
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return
        params.append(run_id)
        conn.execute(f"UPDATE benchmark_runs SET {', '.join(updates)} WHERE run_id = ?", params)
        conn.commit()
    finally:
        conn.close()


def _update_benchmark_question(
    run_id: str,
    seq: int,
    status: str | None = None,
    answer_text: str | None = None,
    trace_json: str | None = None,
    turns: int | None = None,
    duration_s: float | None = None,
    cost_usd: float | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> None:
    conn = _get_db()
    try:
        updates, params = [], []
        for col, val in (
            ("status", status),
            ("answer_text", answer_text),
            ("trace_json", trace_json),
            ("turns", turns),
            ("duration_s", duration_s),
            ("cost_usd", cost_usd),
            ("started_at", started_at),
            ("completed_at", completed_at),
            ("error_message", error_message),
        ):
            if val is not None:
                updates.append(f"{col} = ?")
                params.append(val)
        if not updates:
            return
        params.extend([run_id, seq])
        conn.execute(
            f"UPDATE benchmark_questions SET {', '.join(updates)} WHERE run_id = ? AND seq = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


_RUN_COLS = (
    "run_id, name, domain_hint, source_path, backend, status,"
    " question_count, processed_count, queued_at, started_at, completed_at, error_message"
)


def _row_to_run(row) -> dict:
    return {
        "run_id": row[0], "name": row[1], "domain_hint": row[2], "source_path": row[3],
        "backend": row[4], "status": row[5], "question_count": row[6], "processed_count": row[7],
        "queued_at": row[8], "started_at": row[9], "completed_at": row[10], "error_message": row[11],
    }


def _question_summary_rows(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT seq, qid, title, status, turns, duration_s, cost_usd, error_message"
        " FROM benchmark_questions WHERE run_id = ? ORDER BY seq",
        (run_id,),
    ).fetchall()
    return [
        {
            "seq": r[0], "qid": r[1], "title": r[2], "status": r[3],
            "turns": r[4], "duration_s": r[5], "cost_usd": r[6], "error_message": r[7],
        }
        for r in rows
    ]


def _fetch_active_benchmark_runs() -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            f"SELECT {_RUN_COLS} FROM benchmark_runs WHERE status IN ('queued','processing')"
            " ORDER BY queued_at DESC LIMIT 20"
        ).fetchall()
        result = []
        for row in rows:
            run = _row_to_run(row)
            run["questions"] = _question_summary_rows(conn, run["run_id"])
            result.append(run)
        return result
    finally:
        conn.close()


def _fetch_completed_benchmark_runs(limit: int = 100) -> list[dict]:
    conn = _get_db()
    try:
        rows = conn.execute(
            f"SELECT {_RUN_COLS} FROM benchmark_runs"
            " WHERE status IN ('completed','failed','cancelled')"
            " ORDER BY completed_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def _list_benchmark_runs(status_filter: str | None = None) -> list[dict]:
    conn = _get_db()
    try:
        if status_filter:
            rows = conn.execute(
                f"SELECT {_RUN_COLS} FROM benchmark_runs WHERE status = ?"
                " ORDER BY queued_at DESC LIMIT 50",
                (status_filter,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_RUN_COLS} FROM benchmark_runs ORDER BY queued_at DESC LIMIT 50"
            ).fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def _get_benchmark_run(run_id: str) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            f"SELECT {_RUN_COLS} FROM benchmark_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if not row:
            return None
        run = _row_to_run(row)
        run["questions"] = _question_summary_rows(conn, run_id)
        return run
    finally:
        conn.close()


def _get_benchmark_question(run_id: str, seq: int) -> dict | None:
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT seq, qid, title, question, eval_comment, status, answer_text, trace_json,"
            " turns, duration_s, cost_usd, started_at, completed_at, error_message"
            " FROM benchmark_questions WHERE run_id = ? AND seq = ?",
            (run_id, seq),
        ).fetchone()
        if not row:
            return None
        trace = json.loads(row[7]) if row[7] else []
        return {
            "seq": row[0], "qid": row[1], "title": row[2], "question": row[3],
            "eval_comment": row[4], "status": row[5], "answer_text": row[6], "trace": trace,
            "turns": row[8], "duration_s": row[9], "cost_usd": row[10],
            "started_at": row[11], "completed_at": row[12], "error_message": row[13],
        }
    finally:
        conn.close()


def _cancel_benchmark_run(run_id: str) -> dict:
    conn = _get_db()
    try:
        row = conn.execute("SELECT status FROM benchmark_runs WHERE run_id = ?", (run_id,)).fetchone()
        if not row:
            raise ValueError(f"Benchmark run '{run_id}' not found")
        if row[0] not in ("queued", "processing"):
            return {"run_id": run_id, "cancelled": False, "status": row[0]}
        conn.execute(
            "UPDATE benchmark_runs SET status = 'cancelled', completed_at = ? WHERE run_id = ?",
            (datetime.now().isoformat(), run_id),
        )
        conn.commit()
        return {"run_id": run_id, "cancelled": True, "status": "cancelled"}
    finally:
        conn.close()


# ── export ───────────────────────────────────────────────────────────────

def _export_benchmark_json(run_id: str) -> dict | None:
    run = _get_benchmark_run(run_id)
    if run is None:
        return None
    questions = [_get_benchmark_question(run_id, q["seq"]) for q in run["questions"]]
    run["questions"] = questions
    return run


def _export_benchmark_markdown(run_id: str) -> str | None:
    run = _get_benchmark_run(run_id)
    if run is None:
        return None
    lines = [f"# Benchmark run: {run['name'] or run['run_id']}", ""]
    lines.append(f"- Source: `{run['source_path']}`")
    lines.append(f"- Domain hint: {run['domain_hint'] or '(none — agent routed itself)'}")
    lines.append(f"- Backend: {run['backend']}")
    lines.append(f"- Status: {run['status']} ({run['processed_count']}/{run['question_count']})")
    lines.append("")
    for summary in run["questions"]:
        q = _get_benchmark_question(run_id, summary["seq"])
        title = q["title"] or q["qid"]
        lines.append(f"## {q['qid']} — {title}")
        lines.append("")
        lines.append(f"**Question:** {q['question']}")
        lines.append("")
        lines.append(f"**Answer** ({q['status']}, {q['turns'] or 0} turns, "
                      f"{q['duration_s'] or 0}s, ${q['cost_usd'] or 0:.4f}):")
        lines.append("")
        lines.append(q["answer_text"] or f"_(no answer — {q['error_message'] or q['status']})_")
        lines.append("")
        if q["eval_comment"]:
            lines.append(f"**Evaluation comment (grading notes, not shown to artmind):** {q['eval_comment']}")
            lines.append("")
    return "\n".join(lines)


# ── run orchestration ────────────────────────────────────────────────────

async def _run_benchmark(run_id: str) -> None:
    """Run every queued question in ``run_id`` sequentially against a fresh
    agent backend each time, recording the answer, trace, and cost as each
    question completes. Continues past a single failed question; stops early
    if the run is cancelled between questions.
    """
    # Lazy import: keeps this module (and its parser/CRUD, which are unit
    # tested on their own) importable without the Claude Agent SDK installed.
    from artmind.webui.backends import create_backend
    from artmind.webui.profiles import BENCHMARK_PROFILE

    run = _get_benchmark_run(run_id)
    if run is None:
        return
    _update_benchmark_run(run_id, status="processing", started_at=datetime.now().isoformat())

    for summary in run["questions"]:
        current = _get_benchmark_run(run_id)
        if current is None or current["status"] == "cancelled":
            return

        seq = summary["seq"]
        question = _get_benchmark_question(run_id, seq)
        prompt = question["question"]
        if run["domain_hint"]:
            prompt = f"Domain: {run['domain_hint']}\n\n{prompt}"

        started_at = datetime.now().isoformat()
        _update_benchmark_question(run_id, seq, status="processing", started_at=started_at)

        backend_client = create_backend(run["backend"], BENCHMARK_PROFILE)
        answer_parts: list[str] = []
        trace: list[dict] = []
        turns = duration_s = cost_usd = None
        error_message = None
        try:
            await backend_client.connect()
            await backend_client.query(prompt)
            async for event in backend_client.receive_events():
                etype = event.get("type")
                if etype == "text_delta":
                    answer_parts.append(event.get("text", ""))
                elif etype == "tool_call":
                    trace.append({"type": "tool_call", "name": event.get("name"), "input": event.get("input")})
                elif etype == "tool_result":
                    trace.append({"type": "tool_result", "content": event.get("content")})
                elif etype == "turn_done":
                    turns = event.get("turns")
                    duration_s = event.get("duration_s")
                    cost_usd = event.get("cost")
                elif etype == "error":
                    error_message = event.get("message")
        except Exception as exc:
            error_message = str(exc)
        finally:
            try:
                await backend_client.disconnect()
            except Exception:
                pass

        status = "failed" if error_message else "completed"
        _update_benchmark_question(
            run_id, seq,
            status=status,
            answer_text="".join(answer_parts) or None,
            trace_json=json.dumps(trace) if trace else None,
            turns=turns, duration_s=duration_s, cost_usd=cost_usd,
            completed_at=datetime.now().isoformat(),
            error_message=error_message,
        )
        _update_benchmark_run(run_id, processed_count=seq)

    _update_benchmark_run(run_id, status="completed", completed_at=datetime.now().isoformat())
