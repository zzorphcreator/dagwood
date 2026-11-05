"""Live check of the Composio integration. Needs COMPOSIO_API_KEY in .env.

    .venv/bin/python scripts/check_composio.py [toolkit ...]   (default: composio_search)

Verifies, against the real service:
  1. the catalog search works;
  2. connection status is reported for the toolkits;
  3. the MCP toolset an agent would get exposes only scoped tools: no remote sandbox
     or bash, and no tools that let the agent start auth flows itself.
Exits non-zero if a forbidden tool is exposed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from orchestrator.composio_source import ComposioSource  # noqa: E402

FORBIDDEN = ("WORKBENCH", "BASH", "MANAGE_CONNECTIONS", "INITIATE_CONNECTION")


async def main(slugs: list[str]) -> int:
    source = ComposioSource.from_env()
    if source is None:
        print("COMPOSIO_API_KEY not set (or composio not installed in this Python).")
        return 2
    print(f"user_id={source.user_id} allow_destructive={source.allow_destructive} preset={source.session_preset}")

    found = source.search(slugs[0], 5)
    print(f"\n1. catalog search {slugs[0]!r}: {[t.id for t in found]}")

    print("\n2. connections:")
    for status in source.connections(slugs):
        print(f"   {status.id}: connected={status.connected} needs_auth={status.needs_auth}")

    toolset = source.toolset(slugs)
    async with toolset:
        tools = await toolset.list_tools()
    names = sorted(t.name for t in tools)
    print(f"\n3. tools exposed to an agent ({len(names)}):")
    for name in names:
        print(f"   {name}")
    leaked = [n for n in names if any(f in n.upper() for f in FORBIDDEN)]
    if leaked:
        print(f"\nFAIL: forbidden tools exposed: {leaked}")
        return 1
    print("\nOK: no sandbox or connection-management tools exposed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:] or ["composio_search"])))
