"""Composio as a ToolSource: catalog search, connection status, and per-node MCP toolsets.

Configuration (env):
  COMPOSIO_API_KEY            required to enable this source
  COMPOSIO_USER_ID            whose connected accounts tools act as (default "default")
  COMPOSIO_ALLOW_DESTRUCTIVE  "true" to allow tools tagged destructiveHint (default off until
                              per-call approval exists)
  COMPOSIO_SESSION_PRESET     "direct_tools" to expose toolkit actions directly instead of the
                              default search-first meta tools
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

from pydantic_ai.mcp import MCPToolset

from .catalog import ConnectionStatus, ToolkitInfo
from .schema import ToolCallRecord

SOURCE = "composio"

MULTI_EXECUTE = "COMPOSIO_MULTI_EXECUTE_TOOL"
DISCOVERY_TOOLS = {"COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS"}


def _loads(raw: str | None) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _is_empty(value: Any) -> bool:
    return value is None or value == {} or value == [] or value == ""


def _empty_note(data: Any) -> str | None:
    """Composio's explanation when an action succeeded but returned nothing, else None."""
    if not isinstance(data, dict):
        return None
    message = data.get("composio_execution_message")
    if message and _is_empty(data.get("results")):
        return str(message)
    return None


def expand_tool_call(record: ToolCallRecord) -> list[ToolCallRecord]:
    """Show what a Composio session actually did.

    Search-first sessions run real actions through COMPOSIO_MULTI_EXECUTE_TOOL, so the
    wrapper call is replaced by one record per action (slug, its arguments, its own result).
    Composio's tool lookups are marked as discovery. Anything unrecognised passes through.
    """
    if record.name in DISCOVERY_TOOLS:
        return [record.model_copy(update={"kind": "discovery"})]
    if record.name != MULTI_EXECUTE:
        return [record]
    tools = (_loads(record.args) or {}).get("tools")
    if not isinstance(tools, list) or not tools:
        return [record]
    payload = _loads(record.result)
    results = payload.get("data", {}).get("results") if isinstance(payload, dict) else None
    by_index = {r.get("index", i): r for i, r in enumerate(results)} if isinstance(results, list) else {}
    expanded = []
    for i, tool in enumerate(tools):
        entry = by_index.get(i)
        note = None
        if entry is None:  # no parseable per-action result: keep whatever came back
            ok, result = record.ok, record.result
        elif "error" in entry:
            ok, result = False, str(entry["error"])
        else:
            response = entry.get("response", {})
            ok = bool(response.get("successful", True))
            data = response.get("data", response)
            result = json.dumps(data) if ok else str(response.get("error", response))
            note = _empty_note(data) if ok else None
        expanded.append(
            ToolCallRecord(
                name=str(tool.get("tool_slug", "?")),
                args=json.dumps(tool.get("arguments", {})),
                result=result,
                ok=ok,
                via=MULTI_EXECUTE,
                empty=note is not None,
                note=note,
            )
        )
    return expanded


class ComposioSource:
    name = SOURCE

    def __init__(
        self,
        client: Any,
        user_id: str = "default",
        allow_destructive: bool = False,
        session_preset: str | None = None,
    ) -> None:
        self._client = client  # composio.Composio, injectable for tests
        self.user_id = user_id
        self.allow_destructive = allow_destructive
        self.session_preset = session_preset

    @classmethod
    def from_env(cls) -> ComposioSource | None:
        api_key = os.environ.get("COMPOSIO_API_KEY")
        if not api_key:
            return None
        try:
            from composio import Composio  # imported lazily: only needed when configured
        except ImportError:
            logging.getLogger(__name__).warning(
                "COMPOSIO_API_KEY is set but the composio package isn't installed in this Python (%s); "
                "Composio toolkits are disabled. Install it or run the server from .venv.",
                sys.executable,
            )
            return None
        return cls(
            Composio(api_key=api_key),
            user_id=os.environ.get("COMPOSIO_USER_ID", "default"),
            allow_destructive=os.environ.get("COMPOSIO_ALLOW_DESTRUCTIVE", "").lower() == "true",
            session_preset=os.environ.get("COMPOSIO_SESSION_PRESET") or None,
        )

    def defaults(self) -> list[ToolkitInfo]:
        """Web search needs no account and is what most research tasks are missing."""
        return [
            ToolkitInfo(
                id=f"{SOURCE}:composio_search",
                name="Composio Search",
                description="Web, news, finance and academic search for current, real-time information.",
                source=SOURCE,
                needs_auth=False,
            )
        ]

    def _info(self, item: Any) -> ToolkitInfo:
        return ToolkitInfo(
            id=f"{SOURCE}:{item.slug}",
            name=item.name,
            description=item.meta.description,
            source=SOURCE,
            needs_auth=not bool(item.no_auth),
            logo=item.meta.logo or None,
        )

    def search(self, query: str | None, limit: int) -> list[ToolkitInfo]:
        params: dict[str, Any] = {"limit": limit, "sort_by": "usage"}
        if query:
            params["search"] = query
        return [self._info(item) for item in self._client.client.toolkits.list(**params).items]

    def info(self, slug: str) -> ToolkitInfo:
        for item in self._client.client.toolkits.list(search=slug, limit=50).items:
            if item.slug == slug:
                return self._info(item)
        raise KeyError(f"unknown Composio toolkit {slug!r}")

    def _session(self, slugs: list[str]) -> Any:
        """A session limited to `slugs`, without Composio's extra built-in capabilities.

        Sessions otherwise include a remote code sandbox and tools that let the agent start
        auth flows itself; neither should be reachable unless a reviewer granted it.
        """
        kwargs: dict[str, Any] = {
            "user_id": self.user_id,
            "toolkits": list(slugs),
            "sandbox": {"enable": False},
            "manage_connections": False,
            "mcp": True,
        }
        if not self.allow_destructive:
            kwargs["tags"] = {"disable": ["destructiveHint"]}
        if self.session_preset:
            kwargs["session_preset"] = self.session_preset
        return self._client.create(**kwargs)

    def connections(self, slugs: list[str]) -> list[ConnectionStatus]:
        details = self._session(slugs).toolkits(toolkits=list(slugs))
        return [
            ConnectionStatus(
                id=f"{SOURCE}:{item.slug}",
                name=item.name,
                connected=bool(item.is_no_auth or (item.connection and item.connection.is_active)),
                needs_auth=not item.is_no_auth,
            )
            for item in details.items
        ]

    def connect_link(self, slug: str) -> str:
        return self._session([slug]).authorize(slug).redirect_url

    def toolset(self, slugs: list[str]) -> MCPToolset:
        mcp = self._session(slugs).mcp
        headers = {k: v for k, v in (mcp.headers or {}).items() if v is not None}
        return MCPToolset(mcp.url, headers=headers or None)
