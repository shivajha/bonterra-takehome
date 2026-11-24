"""Tiny helper: start the server over stdio as a given staff identity and call tools."""
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def server_params(staff_id: str, db_path) -> StdioServerParameters:
    env = {**os.environ, "CB_STAFF_ID": staff_id, "CB_DB_PATH": str(db_path), "CB_LOG_LEVEL": "WARNING"}
    return StdioServerParameters(command=sys.executable, args=["-m", "cb.server"], env=env, cwd=str(PROJECT_ROOT))


@asynccontextmanager
async def session_as(staff_id: str, db_path):
    async with stdio_client(server_params(staff_id, db_path)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def parse(result) -> dict:
    """Return the tool's JSON: structured content on success, the {error: ...} object on failure."""
    if result.is_error:
        text = result.content[0].text
        return json.loads(text[text.index("{"):])
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)