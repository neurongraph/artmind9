"""Claude Agent SDK backend: composes the SDK client with the event mapper."""

from typing import AsyncIterator

from claude_agent_sdk import ClaudeSDKClient

from artmind.webui.agent import EventMapper, agent_options
from artmind.webui.backends.base import UIEvent
from artmind.webui.profiles import AgentProfile, QA_PROFILE


class ClaudeSDKBackend:
    def __init__(self, profile: AgentProfile = QA_PROFILE) -> None:
        self._client = ClaudeSDKClient(agent_options(profile))

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def query(self, prompt: str) -> None:
        await self._client.query(prompt)

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        mapper = EventMapper()
        async for message in self._client.receive_response():
            for event in mapper.map(message):
                yield event

    async def interrupt(self) -> None:
        await self._client.interrupt()
