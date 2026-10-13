"""Launcher for MCP clients (Claude Desktop, Cursor, ...) that start the server from an unknown working directory.

Point the client at:  <python> <path-to-project>/plant_memory_mcp.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from plant_memory.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
