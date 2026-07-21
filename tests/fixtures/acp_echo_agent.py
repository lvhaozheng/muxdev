from __future__ import annotations

import asyncio
import os
from typing import Any
from uuid import uuid4

from acp import Agent, InitializeResponse, NewSessionResponse, PromptResponse, run_agent, text_block, update_agent_message
from acp.interfaces import Client
from acp.schema import (
    AgentAuthCapabilities,
    AgentCapabilities,
    McpServerStdio,
    PermissionOption,
    ToolCallStart,
    ToolCallUpdate,
)


class EchoAgent(Agent):
    _connection: Client

    def __init__(self) -> None:
        self._cancelled: dict[str, asyncio.Event] = {}
        self._mcp_servers: dict[str, list[McpServerStdio]] = {}

    def on_connect(self, connection: Client) -> None:
        self._connection = connection

    async def initialize(self, protocol_version: int, **kwargs: Any) -> InitializeResponse:
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(auth=AgentAuthCapabilities()),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[McpServerStdio] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        del cwd, additional_directories, kwargs
        session_id = uuid4().hex
        self._cancelled[session_id] = asyncio.Event()
        self._mcp_servers[session_id] = list(mcp_servers or [])
        return NewSessionResponse(session_id=session_id)

    async def prompt(self, session_id: str, prompt, **kwargs: Any) -> PromptResponse:
        prompt_text = str(prompt)
        if "Keep this ACP session active" in prompt_text:
            await self._cancelled[session_id].wait()
            return PromptResponse(stop_reason="cancelled")
        if self._mcp_servers.get(session_id):
            await self._call_mcp_fixture(session_id, self._mcp_servers[session_id][0])
        await self._connection.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(tool_call_id="fixture-read", title="Read fixture", kind="read"),
            options=[PermissionOption(option_id="allow", name="Allow once", kind="allow_once")],
        )
        content = '{"summary":"ACP fixture completed","affected_paths":[],"suggested_verification":[]}'
        await self._connection.session_update(
            session_id=session_id,
            update=update_agent_message(text_block(content)),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        if event := self._cancelled.get(session_id):
            event.set()

    async def _call_mcp_fixture(self, session_id: str, server: McpServerStdio) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        tool_call = ToolCallUpdate(
            tool_call_id="fixture-mcp",
            title="muxdev-certification/ping",
            kind="read",
            raw_input={"tool_ref": "muxdev-certification/ping"},
        )
        await self._connection.request_permission(
            session_id=session_id,
            tool_call=tool_call,
            options=[PermissionOption(option_id="allow-mcp", name="Allow MCP", kind="allow_once")],
        )
        environment = {**os.environ, **{item.name: item.value for item in server.env}}
        parameters = StdioServerParameters(
            command=server.command,
            args=list(server.args),
            env=environment,
        )
        async with stdio_client(parameters) as (read, write):
            async with ClientSession(read, write) as mcp_session:
                await mcp_session.initialize()
                result = await mcp_session.call_tool("ping", {})
        await self._connection.session_update(
            session_id=session_id,
            update=ToolCallStart(
                session_update="tool_call",
                tool_call_id="fixture-mcp",
                title="muxdev-certification/ping",
                kind="read",
                status="completed",
                raw_input={"tool_ref": "muxdev-certification/ping"},
                raw_output=result.model_dump(mode="json"),
            ),
        )


async def main() -> None:
    await run_agent(EchoAgent())


if __name__ == "__main__":
    asyncio.run(main())
