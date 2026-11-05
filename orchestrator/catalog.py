"""Tool catalog: the toolkits agents may use, drawn from pluggable sources.

Plans reference catalog ids:
- built-in Python tools by bare name ("calc"), always enabled;
- external toolkits as "<source>:<slug>" ("composio:github"), enabled by the user.

Enabled external toolkits are stored (with their metadata) next to the runs, so
building the planner prompt or validating a plan never needs a network call.
Only runtime work (searching, connection checks, opening toolsets) reaches a source.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from .tools import REGISTRY

BUILTIN = "builtin"


@dataclass
class ToolkitInfo:
    id: str
    name: str
    description: str
    source: str
    needs_auth: bool = False
    logo: str | None = None


@dataclass
class ConnectionStatus:
    id: str
    name: str
    connected: bool
    needs_auth: bool


class ToolSource(Protocol):
    """An external provider of toolkits. Methods may block on the network; the catalog runs them in a thread."""

    name: str

    def search(self, query: str | None, limit: int) -> list[ToolkitInfo]: ...

    def info(self, slug: str) -> ToolkitInfo: ...

    def connections(self, slugs: list[str]) -> list[ConnectionStatus]: ...

    def connect_link(self, slug: str) -> str: ...

    def toolset(self, slugs: list[str]) -> Any:
        """A pydantic-ai toolset exposing exactly these toolkits."""
        ...

    # Optional: `defaults() -> list[ToolkitInfo]`, toolkits enabled once when the source first
    # appears; a later disable by the user is respected.


def split_id(toolkit_id: str) -> tuple[str, str]:
    """("composio", "github") for "composio:github"; ("builtin", "calc") for "calc"."""
    source, sep, slug = toolkit_id.partition(":")
    return (source, slug) if sep else (BUILTIN, toolkit_id)


class Catalog:
    """Built-ins plus user-enabled external toolkits. In-memory when `db_path` is None."""

    def __init__(self, sources: dict[str, ToolSource] | None = None, db_path: str | None = None) -> None:
        self.sources: dict[str, ToolSource] = sources or {}
        self._enabled: dict[str, ToolkitInfo] = {}
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            self._db = sqlite3.connect(db_path, check_same_thread=False)
            self._db.execute("CREATE TABLE IF NOT EXISTS enabled_toolkits (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            self._db.execute("CREATE TABLE IF NOT EXISTS seeded_toolkits (id TEXT PRIMARY KEY)")
            self._db.commit()
            for (raw,) in self._db.execute("SELECT data FROM enabled_toolkits"):
                info = ToolkitInfo(**json.loads(raw))
                self._enabled[info.id] = info
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        seeded: set[str] = set()
        if self._db is not None:
            seeded = {row[0] for row in self._db.execute("SELECT id FROM seeded_toolkits")}
        for source in self.sources.values():
            for info in getattr(source, "defaults", lambda: [])():
                if info.id in seeded:
                    continue
                self.enable(info)
                if self._db is not None:
                    self._db.execute("INSERT OR IGNORE INTO seeded_toolkits (id) VALUES (?)", (info.id,))
                    self._db.commit()

    # --- what the planner and editor may choose from -------------------------------------

    def builtins(self) -> list[ToolkitInfo]:
        return [ToolkitInfo(id=name, name=name, description=desc, source=BUILTIN) for name, (_, desc) in REGISTRY.items()]

    def enabled(self) -> list[ToolkitInfo]:
        """Built-ins, then enabled external toolkits whose source is configured."""
        external = sorted(
            (t for t in self._enabled.values() if t.source in self.sources), key=lambda t: t.name.lower()
        )
        return self.builtins() + external

    def is_enabled(self, toolkit_id: str) -> bool:
        return toolkit_id in self._enabled

    def unknown(self, ids: list[str]) -> list[str]:
        known = {t.id for t in self.enabled()}
        return [i for i in ids if i not in known]

    def source(self, name: str) -> ToolSource:
        if name not in self.sources:
            raise KeyError(f"tool source {name!r} is not configured")
        return self.sources[name]

    # --- enabling ---------------------------------------------------------------------------

    def enable(self, info: ToolkitInfo) -> None:
        self._enabled[info.id] = info
        if self._db is not None:
            self._db.execute(
                "INSERT OR REPLACE INTO enabled_toolkits (id, data) VALUES (?, ?)", (info.id, json.dumps(asdict(info)))
            )
            self._db.commit()

    def disable(self, toolkit_id: str) -> bool:
        removed = self._enabled.pop(toolkit_id, None) is not None
        if removed and self._db is not None:
            self._db.execute("DELETE FROM enabled_toolkits WHERE id = ?", (toolkit_id,))
            self._db.commit()
        return removed

    # --- runtime ----------------------------------------------------------------------------

    def _external_by_source(self, ids: list[str]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for toolkit_id in ids:
            source, slug = split_id(toolkit_id)
            if source != BUILTIN:
                groups.setdefault(source, []).append(slug)
        return groups

    async def toolsets_for(self, ids: list[str]) -> list[Any]:
        """One toolset per external source, scoped to exactly the toolkits in `ids`."""
        toolsets = []
        for source, slugs in self._external_by_source(ids).items():
            toolsets.append(await asyncio.to_thread(self.source(source).toolset, slugs))
        return toolsets

    async def connections_for(self, ids: list[str]) -> list[ConnectionStatus]:
        statuses: list[ConnectionStatus] = []
        for source, slugs in self._external_by_source(ids).items():
            statuses.extend(await asyncio.to_thread(self.source(source).connections, slugs))
        return statuses
