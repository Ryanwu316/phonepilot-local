import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from phonepilot.server import ROOT


async def test_real_stdio_mcp_with_demo_server(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cmd = [sys.executable, "-m", "phonepilot.server", "--demo", "--port", str(port), "--state-dir", str(tmp_path)]
    process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        runtime = tmp_path / "runtime.json"
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError("demo server exited early")
            if runtime.exists():
                try:
                    state = json.loads(runtime.read_text())
                    async with httpx.AsyncClient(trust_env=False) as http:
                        response = await http.get(state["url"] + "/api/status", headers={"X-PhonePilot-Key": state["token"]})
                    if response.status_code == 200:
                        break
                except (ValueError, httpx.RequestError):
                    pass
            await asyncio.sleep(.05)
        else:
            raise AssertionError("server startup timed out")
        assert runtime.stat().st_mode & 0o777 == 0o600
        original = runtime.read_bytes()
        # A duplicate launch must leave the original runtime credentials intact.
        duplicate = await asyncio.create_subprocess_exec(*cmd, cwd=ROOT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        assert await asyncio.wait_for(duplicate.wait(), 10) == 1
        assert runtime.read_bytes() == original
        async with httpx.AsyncClient(trust_env=False) as http:
            response = await http.post(state["url"] + "/api/connect", headers={"X-PhonePilot-Key": state["token"]}, json={"device_id": "demo"})
            assert response.status_code == 202
        params = StdioServerParameters(command=sys.executable, args=["-m", "phonepilot.mcp_server"], cwd=str(ROOT),
            env={**os.environ, "PHONEPILOT_STATE_DIR": str(tmp_path), "PYTHONPATH": str(ROOT)})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 10
                status = await session.call_tool("phone_status", {})
                assert not status.isError and '"demo": true' in status.content[0].text
                screenshot = await session.call_tool("phone_screenshot", {})
                assert not screenshot.isError
                assert any(block.type == "image" and block.mimeType == "image/png" for block in screenshot.content)
                note = json.loads(next(block.text for block in screenshot.content if block.type == "text"))
                assert (note["width"], note["height"]) == (390, 844)
                args = {"x": 30, "y": 30, "observation_id": note["observation_id"]}
                tap = await session.call_tool("phone_tap", args)
                assert not tap.isError
                stale = await session.call_tool("phone_tap", args)
                assert stale.isError
    finally:
        process.terminate()
        try:
            await asyncio.wait_for(asyncio.to_thread(process.wait), 15)
        except TimeoutError:
            process.kill()
            process.wait()
    assert not (tmp_path / "runtime.json").exists()
