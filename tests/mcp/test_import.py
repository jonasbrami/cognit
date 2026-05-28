def test_mcp_package_imports():
    import cognit.mcp  # noqa: F401


def test_fastmcp_available():
    from mcp.server.fastmcp import FastMCP  # noqa: F401
