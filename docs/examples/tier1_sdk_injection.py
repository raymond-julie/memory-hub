"""Tier 1: SDK-based memory injection for custom agents.

Loads memories at startup via the MemoryHub SDK and assembles them into
a <memory> block suitable for system prompt injection. No MCP tools needed
at runtime -- the model sees memories as plain text context.

Usage:
    export MEMORYHUB_URL="https://memory-hub-mcp-memory-hub-mcp.apps.example.com/mcp/"
    export MEMORYHUB_API_KEY="mh-dev-abc123"
    python tier1_sdk_injection.py --project my-project --query "deployment preferences"
"""

import argparse
import asyncio
import os
import sys

from memoryhub import MemoryHubClient


async def build_memory_block(
    client: MemoryHubClient,
    query: str,
    project_id: str | None = None,
    max_results: int = 10,
) -> str:
    """Search memories and format as a <memory> block for prompt injection."""
    result = await client.search(
        query,
        project_id=project_id,
        max_results=max_results,
    )

    if not result.results:
        return ""

    lines = ["<memory>"]
    for mem in result.results:
        lines.append(f"- {mem.content}")
    lines.append("</memory>")
    return "\n".join(lines)


async def main(query: str, project_id: str | None, url: str, api_key: str) -> None:
    client = MemoryHubClient(url=url, api_key=api_key)

    async with client:
        block = await build_memory_block(client, query, project_id=project_id)

    if block:
        print(block)  # noqa: T201
    else:
        print("No memories found.", file=sys.stderr)  # noqa: T201
        sys.exit(1)


# -- Framework-native variant (fips-agents) --
#
# In fips-agents, the agent base class provides self.memory as a
# pre-configured MemoryHubClient. The startup pattern looks like:
#
#     async def on_session_start(self):
#         result = await self.memory.search(
#             "project context", project_id=self.project_id
#         )
#         block = "\n".join(f"- {m.content}" for m in result.results)
#         self.system_prompt += f"\n<memory>\n{block}\n</memory>"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MemoryHub Tier 1 SDK injection")
    parser.add_argument("--query", default="project context and preferences")
    parser.add_argument("--project", default=None, help="Project ID to filter by")
    parser.add_argument("--url", default=os.environ.get("MEMORYHUB_URL"))
    parser.add_argument("--api-key", default=os.environ.get("MEMORYHUB_API_KEY"))
    args = parser.parse_args()

    if not args.url or not args.api_key:
        print("Set MEMORYHUB_URL and MEMORYHUB_API_KEY, or pass --url and --api-key.", file=sys.stderr)  # noqa: T201
        sys.exit(1)

    asyncio.run(main(args.query, args.project, args.url, args.api_key))
