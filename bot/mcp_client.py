"""瑞幸 MCP streamable-http 客户端。

每个用户用自己的 Bearer Token 连 gwmcp 端点；按 token 缓存会话，单用户调用串行化，
遇 401/404/断流则丢弃重连重试一次。
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from core.config import get_settings
from core.luckin import mcp_endpoint

log = logging.getLogger("mcp")


class MCPToolError(RuntimeError):
    """工具级错误 (result.isError) 或协议错误。"""


def _first_text(result: Any) -> str:
    for block in getattr(result, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


@dataclass
class _Conn:
    session: ClientSession
    stack: AsyncExitStack
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LuckinMCPClient:
    def __init__(self, env: Optional[str] = None) -> None:
        self._endpoint = mcp_endpoint(env or get_settings().luckin_env)
        self._conns: dict[str, _Conn] = {}
        self._guard = asyncio.Lock()  # 保护 _conns 字典

    async def _open(self, token: str) -> _Conn:
        stack = AsyncExitStack()
        read, write, _ = await stack.enter_async_context(
            streamablehttp_client(self._endpoint, headers={"Authorization": f"Bearer {token}"})
        )
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        log.info("opened MCP session for token …%s", token[-6:])
        return _Conn(session=session, stack=stack)

    async def _get(self, token: str) -> _Conn:
        conn = self._conns.get(token)
        if conn is None:
            async with self._guard:
                conn = self._conns.get(token)
                if conn is None:
                    conn = await self._open(token)
                    self._conns[token] = conn
        return conn

    async def _drop(self, token: str) -> None:
        async with self._guard:
            conn = self._conns.pop(token, None)
        if conn is not None:
            try:
                await conn.stack.aclose()
            except Exception:
                pass

    async def call_tool(self, token: str, name: str, arguments: dict) -> Any:
        """调用工具，返回解析后的 data（dict/list/str）。失败抛 MCPToolError。"""
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            conn = await self._get(token)
            try:
                async with conn.lock:
                    result = await conn.session.call_tool(name, arguments=arguments)
                return self._parse(result)
            except MCPToolError:
                raise  # 业务错误不重试
            except Exception as e:  # 连接/协议错误 → 重连重试一次
                last_exc = e
                log.warning("MCP %s failed (attempt %d): %s", name, attempt, e)
                await self._drop(token)
        raise MCPToolError(f"MCP {name} 连接失败: {last_exc}")

    @staticmethod
    def _parse(result: Any) -> Any:
        if getattr(result, "isError", False):
            raise MCPToolError(_first_text(result) or "tool returned isError")
        structured = getattr(result, "structuredContent", None)
        if structured is not None:
            return structured
        text = _first_text(result)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    async def aclose(self) -> None:
        for token in list(self._conns):
            await self._drop(token)
