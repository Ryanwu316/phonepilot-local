"""stdio MCP client of the running local hub. It never opens a second device tunnel."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import httpx
from mcp.server.fastmcp import FastMCP, Image

from .server import DEFAULT_STATE

mcp = FastMCP("PhonePilot Local", instructions=(
    "控制用户在 PhonePilot 控制台主动连接的 iPhone。先获取状态和截图，再按截图坐标操作；"
    "每次操作后重新截图验证。每张截图返回 observation_id，像素坐标操作必须使用它。"
    "接口返回成功仅代表指令已执行，不代表业务结果完成。"
    "连接恢复期间停止操作；恢复后重新截图。超时或中断的输入不可重放，先观察实际结果。"
    "不要输入设备解锁密码、支付密码或自行确认支付。涉及发送消息、下单或共享信息时遵守用户授权。"
    "demo=true 表示演示，不得声称已操作真实手机。"
))


async def request(method: str, path: str, data=None):
    state = Path(os.getenv("PHONEPILOT_STATE_DIR", DEFAULT_STATE))
    try:
        config = json.loads((state / "runtime.json").read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("请先打开 PhonePilot，并在控制台连接 iPhone。") from exc
    url = config.get("url", "")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and
                 parsed.port is not None and 1024 <= parsed.port <= 65535 and
                 not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment)))
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("本地服务地址无效")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(55, connect=5), trust_env=False) as client:
            response = await client.request(method, url + path,
                headers={"X-PhonePilot-Key": config["token"]}, json=data)
    except httpx.TimeoutException as exc:
        raise RuntimeError("响应超时，操作结果尚未确认。请重新观察手机画面，不要重复发送上一条操作。") from exc
    except httpx.RequestError as exc:
        raise RuntimeError("无法连接 PhonePilot。请先打开本地控制台。") from exc
    if response.is_error:
        try:
            message = response.json().get("detail", "操作未完成")
        except ValueError:
            message = "操作未完成"
        raise RuntimeError(str(message))
    return response


@mcp.tool()
async def phone_status() -> dict:
    """查看真实连接状态。demo=true 表示演示。不能据此认定手机任务完成。"""
    return (await request("GET", "/api/status")).json()


@mcp.tool()
async def phone_screenshot() -> list:
    """读取当前画面（最长边1280像素），同时返回图像尺寸和60秒有效的 observation_id。"""
    response = await request("GET", "/api/screenshot?max_size=1280")
    note = json.dumps({"observation_id": response.headers["x-observation-id"],
                       "width": int(response.headers["x-image-width"]),
                       "height": int(response.headers["x-image-height"]),
                       "note": "坐标以这张截图为准；操作后必须重新截图"}, ensure_ascii=False)
    return [note, Image(data=response.content, format="png")]


@mcp.tool()
async def phone_tap(x: float, y: float, observation_id: str) -> dict:
    """点击最新截图中的像素坐标；禁止凭印象猜坐标。"""
    return (await request("POST", "/api/action", {"kind": "tap", "x": x, "y": y,
        "space": "pixels", "observation_id": observation_id})).json()


@mcp.tool()
async def phone_swipe(x: float, y: float, x2: float, y2: float, observation_id: str, duration: float = 0.4) -> dict:
    """在最新截图上按像素坐标拖动。duration 是0.05到3秒。"""
    return (await request("POST", "/api/action", {"kind": "drag", "x": x, "y": y, "x2": x2,
        "y2": y2, "space": "pixels", "observation_id": observation_id, "duration": duration})).json()


@mcp.tool()
async def phone_long_press(x: float, y: float, observation_id: str, duration: float = 0.8) -> dict:
    """在最新截图上的指定像素坐标长按。"""
    return (await request("POST", "/api/action", {"kind": "long_press", "x": x, "y": y,
        "space": "pixels", "observation_id": observation_id, "duration": duration})).json()


@mcp.tool()
async def phone_type_text(text: str) -> dict:
    """向已聚焦的输入框输入文字。不自动提交。非ASCII文字通过粘贴输入，会替换手机剪贴板。"""
    return (await request("POST", "/api/action", {"kind": "type", "text": text})).json()


@mcp.tool()
async def phone_press_key(name: Literal["enter", "backspace", "tab", "escape", "left", "right", "up", "down"]) -> dict:
    """发送键盘按键。Enter可能提交表单，使用前确认用户授权。"""
    return (await request("POST", "/api/action", {"kind": "key", "name": name})).json()


@mcp.tool()
async def phone_press_button(name: Literal["home", "lock", "volume-up", "volume-down", "mute"]) -> dict:
    """按主屏幕、锁屏、音量或静音键。不能解锁手机。"""
    return (await request("POST", "/api/action", {"kind": "button", "name": name})).json()


@mcp.tool()
async def phone_list_apps() -> dict:
    """列出连接设备上的可见App及其标识。"""
    return (await request("GET", "/api/apps")).json()


@mcp.tool()
async def phone_open_app(bundle_id: str) -> dict:
    """打开指定bundle_id的App，不强制结束已运行的App。打开后重新截图观察。"""
    return (await request("POST", "/api/action", {"kind": "launch", "bundle_id": bundle_id})).json()


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
