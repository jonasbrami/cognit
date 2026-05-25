"""`python -m cognit.mcp` entry point — delegates to the MCP server's main().

`cognit.mcp.server` is scaffolded in a later task; until then the import is
statically unresolved, so it carries a type-ignore that MUST be removed once
`server.py` exists (mypy strict warns on unused ignores).
"""

from cognit.mcp.server import main  # type: ignore[import-not-found]

if __name__ == "__main__":
    main()
