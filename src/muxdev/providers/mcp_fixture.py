"""Read-only MCP fixture used by live provider certification."""

from __future__ import annotations


def main() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("MCP fixture requires muxdev[interop]") from exc

    server = FastMCP("muxdev-certification-fixture", json_response=True)

    @server.tool(name="ping", description="Return a deterministic read-only certification value.")
    def ping() -> dict[str, str]:
        return {"fixture": "muxdev-read-only", "status": "ok"}

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
