from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import fcntl
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import time
import tempfile
import ipaddress
from typing import Literal
from collections import deque
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.base import BaseHTTPMiddleware

from . import __version__
from .native import DemoSession, DeviceError, NativeSession, discover_devices, friendly_error
from .doctor import diagnose

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_STATE = Path(os.getenv("PHONEPILOT_STATE_DIR", Path.cwd() / "var"))


def clear_runtime(runtime):
    if runtime is not None:
        with contextlib.suppress(OSError, ValueError):
            if json.loads(runtime.read_text()).get("pid") == os.getpid():
                runtime.unlink()


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: str
    x: float | None = None
    y: float | None = None
    x2: float | None = None
    y2: float | None = None
    duration: float = Field(default=0.4, ge=0.05, le=3)
    space: str = "normalized"
    observation_id: str | None = None
    text: str | None = Field(default=None, max_length=10000)
    name: str | None = None
    bundle_id: str | None = None


class Connect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=200)
    transport: Literal["usb", "wifi"] = "usb"
    host: str | None = Field(default=None, max_length=45)

    @model_validator(mode="after")
    def local_wireless_host(self):
        if self.host:
            if self.transport != "wifi":
                raise ValueError("手动地址仅用于无线连接")
            address = ipaddress.IPv4Address(self.host)
            if not any(address in ipaddress.IPv4Network(network) for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")):
                raise ValueError("请填写手机的局域网 IPv4 地址")
            self.host = str(address)
        return self


class Pause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool


def hid_point(x, y, width=1, height=1):
    if x is None or y is None or not all(math.isfinite(v) for v in (x, y, width, height)):
        raise ValueError("请提供有效的坐标")
    if width <= 0 or height <= 0 or not (0 <= x < width and 0 <= y < height):
        raise ValueError("坐标超出画面范围")
    return round(x / width * 65535), round(y / height * 65535)


class Hub:
    HEALTH_INTERVAL = 4
    HEALTH_TIMEOUT = 10
    RECOVERY_DELAYS = (2, 5, 10)
    DEVICE_RETURN_WINDOW = 60
    DEVICE_POLL_INTERVAL = 3

    def __init__(self, demo=False):
        self.demo = demo
        self.session = None
        self.pending = None
        self.connect_task = None
        self.action_lock = asyncio.Lock()
        self.lifecycle_lock = asyncio.Lock()
        self.control_task = None
        self.phase = "disconnected"
        self.device = None
        self.error = None
        self.events = deque(maxlen=60)
        self.observations = {}
        self.paused = False
        self.monitor_task = None
        self.recovery_task = None
        self.recovery_attempt = 0
        self.recovery_stage = None
        self.last_verified = None
        self.session_epoch = 0
        self.auto_recover = False
        self.transport = "usb"
        self.setup_busy = False
        self.device_cache = {}
        self.known_devices = {}
        self.host = None

    def record(self, label, ok=True):
        # No screenshots, typed content, app names, tokens or device IDs in the log.
        self.events.appendleft({"time": time.strftime("%H:%M:%S"), "label": label,
                                "ok": ok, "demo": self.demo})

    def status(self):
        return {"version": __version__, "demo": self.demo, "phase": self.phase,
                "device": self.device, "error": self.error, "paused": self.paused,
                "width": getattr(self.session, "width", 0), "height": getattr(self.session, "height", 0),
                "session_epoch": self.session_epoch, "recovery_attempt": self.recovery_attempt,
                "transport": self.transport, "setup_busy": self.setup_busy,
                "manual_wireless": self.host is not None,
                "recovery_limit": len(self.RECOVERY_DELAYS), "recovery_stage": self.recovery_stage,
                "verified_seconds_ago": None if self.last_verified is None else round(time.monotonic() - self.last_verified, 1),
                "events": list(self.events)}

    async def devices(self, transport="usb"):
        if self.demo:
            return [{"id": "demo", "name": "演示 iPhone", "version": "演示", "transport": "Demo", "trusted": True}]
        devices = await asyncio.wait_for(discover_devices(transport), 12)
        if transport == "usb":
            self.known_devices.update({device["id"]: device for device in devices if device.get("trusted")})
        else:
            for device in devices:
                if device["id"] in self.known_devices:
                    device["name"] = self.known_devices[device["id"]]["name"]
            for device_id, device in self.known_devices.items():
                if not any(row["id"] == device_id for row in devices):
                    devices.append({**device, "transport": "Wi-Fi", "manual_required": True})
        now = time.monotonic()
        self.device_cache = {key: value for key, value in self.device_cache.items() if now - value[0] < 120}
        for device in devices:
            self.device_cache[(transport, device["id"])] = (now, device)
        return devices

    async def start_connect(self, device_id, transport="usb", host=None):
        async with self.lifecycle_lock:
            await self._start_connect(device_id, transport, host)

    async def _start_connect(self, device_id, transport="usb", host=None):
        if transport not in ("usb", "wifi"):
            raise ValueError("请选择数据线或无线连接")
        if self.phase in ("connecting", "connected", "reconnecting") and self.device and self.device["id"] == device_id and transport == self.transport:
            return  # Repeated connect requests reuse the selected session.
        if self.phase in ("connecting", "connected", "reconnecting", "disconnecting"):
            raise ValueError("请先断开当前会话")
        cached = self.device_cache.get((transport, device_id))
        if cached and time.monotonic() - cached[0] < 120:
            picked = cached[1]
        else:
            devices = await self.devices(transport)
            picked = next((d for d in devices if d["id"] == device_id), None)
        if not picked:
            raise ValueError("设备已不在列表中，请刷新后重新选择")
        if not picked.get("trusted"):
            raise ValueError(picked.get("hint") or "请先在 iPhone 上信任这台 Mac")
        if picked.get("manual_required") and not host:
            raise ValueError("已找到配对记录，但没有发现无线地址。请填写手机 Wi-Fi 设置中的 IP 地址。")
        self.phase, self.error, self.device = "connecting", None, picked
        self.transport = transport
        self.host = host
        self.paused = False
        self.auto_recover = True
        self.recovery_attempt = 0
        self.recovery_stage = "connecting"
        self.connect_task = asyncio.create_task(self._connect(device_id))

    async def _connect(self, device_id):
        session = DemoSession() if self.demo else NativeSession(transport=self.transport, host=self.host)
        self.pending = session
        try:
            async with self.action_lock:
                await asyncio.wait_for(session.connect(device_id), 75)
                self.session = session
                self.phase = "connected"
                self.session_epoch += 1
                self.last_verified = time.monotonic()
                self.recovery_stage = None
                self.record("连接已建立")
                if not self.monitor_task or self.monitor_task.done():
                    self.monitor_task = asyncio.create_task(self._monitor())
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.close(), 25)
            raise
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(session.close(), 25)
            self.session = None
            self.phase, self.error = "error", friendly_error(exc)
            self.recovery_stage = None
            self.record("连接未完成", False)
        finally:
            self.pending = None

    async def _monitor(self):
        failures = 0
        while True:
            await asyncio.sleep(self.HEALTH_INTERVAL)
            if self.phase != "connected" or self.action_lock.locked():
                continue
            try:
                async with self.action_lock:
                    session = self.require_session()
                    await asyncio.wait_for(session.screenshot(), self.HEALTH_TIMEOUT)
                    self.last_verified = time.monotonic()
                failures = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failures += 1
                if failures >= 2:
                    self.connection_lost(exc)
                    failures = 0

    def connection_lost(self, exc):
        if self.phase != "connected" or not self.auto_recover:
            return
        self.phase = "reconnecting"
        self.error = friendly_error(exc)
        self.recovery_stage = "closing"
        self.observations.clear()
        self.session_epoch += 1
        self.record("连接中断，正在恢复", False)
        self.recovery_task = asyncio.create_task(self._recover())

    async def _recover(self):
        # Reconnect only this explicitly selected device. Never replay an action.
        try:
            await self.stop_control()
            async with self.action_lock:
                old, self.session = self.session, None
                if old:
                    await asyncio.wait_for(old.close(), 25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.phase, self.recovery_stage = "error", "stopped"
            self.error = "原连接未能清理完成，请检查手机后重新连接。"
            self.record("连接恢复已停止", False)
            return
        device_id = self.device["id"]
        deadline = time.monotonic() + self.DEVICE_RETURN_WINDOW
        try:
            for attempt, delay in enumerate(self.RECOVERY_DELAYS, 1):
                self.recovery_attempt = attempt
                self.recovery_stage = "waiting_device"
                await asyncio.sleep(delay)
                while True:
                    if self.host:
                        picked = self.device
                        break
                    devices = await self.devices(self.transport)
                    picked = next((d for d in devices if d["id"] == device_id and d.get("trusted") and not d.get("manual_required")), None)
                    if picked:
                        break
                    if time.monotonic() >= deadline:
                        raise DeviceError("未发现原设备，请检查数据线、解锁手机后点击重新连接。")
                    await asyncio.sleep(self.DEVICE_POLL_INTERVAL)
                self.recovery_stage = "connecting"
                candidate = DemoSession() if self.demo else NativeSession(transport=self.transport, host=self.host)
                self.pending = candidate
                try:
                    async with self.action_lock:
                        await asyncio.wait_for(candidate.connect(device_id), 60)
                        self.session = candidate
                        self.device = picked
                        self.last_verified = time.monotonic()
                        self.session_epoch += 1
                        self.phase, self.error, self.recovery_stage = "connected", None, None
                        self.record("原设备连接已恢复，请重新观察画面")
                    return
                except BaseException as exc:
                    await candidate.close()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    self.error = friendly_error(exc)
                finally:
                    self.pending = None
            self.phase = "error"
            self.recovery_stage = "stopped"
            self.record("自动恢复已停止，请检查后重新连接", False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.phase, self.recovery_stage = "error", "stopped"
            self.error = str(exc) if isinstance(exc, DeviceError) else friendly_error(exc)
            self.record("自动恢复已停止，请检查后重新连接", False)

    async def retry(self):
        async with self.lifecycle_lock:
            if self.phase in ("connecting", "reconnecting", "connected"):
                return
            if not self.device:
                raise ValueError("请先选择要连接的 iPhone")
            device, paused, transport, host = self.device, self.paused, self.transport, self.host
            await self._disconnect()
            try:
                await self._start_connect(device["id"], transport, host)
            except Exception as exc:
                self.device, self.phase = device, "error"
                self.error = str(exc) if isinstance(exc, ValueError) else friendly_error(exc)
                raise
            finally:
                self.paused = paused

    async def disconnect(self):
        async with self.lifecycle_lock:
            await self._disconnect()

    async def stop_control(self):
        task = self.control_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def pause(self, paused):
        self.paused = paused
        self.observations.clear()
        if paused:
            await self.stop_control()
            self.record("控制已暂停")
        else:
            self.record("控制已恢复")

    async def _disconnect(self):
        self.auto_recover = False
        self.phase = "disconnecting"
        for task in (self.monitor_task, self.recovery_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self.monitor_task = self.recovery_task = None
        await self.stop_control()
        if self.connect_task and not self.connect_task.done():
            self.connect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.connect_task
        async with self.action_lock:
            if self.session:
                await self.session.close()
            self.session = None
            self.observations.clear()
            self.phase, self.error, self.device = "disconnected", None, None
            self.recovery_stage, self.last_verified = None, None
            self.recovery_attempt = 0
            self.session_epoch += 1
            self.record("会话已断开")

    def require_session(self, control=False):
        if self.phase != "connected" or self.session is None:
            if self.phase == "reconnecting":
                raise DeviceError("连接正在恢复；未完成的操作不会重试，请等待新画面。")
            raise DeviceError("请先在控制台连接 iPhone")
        if control and self.paused:
            raise DeviceError("控制已暂停，请在控制台恢复")
        return self.session

    async def capture(self, max_size=1280):
        async with self.action_lock:
            session = self.require_session()
            try:
                image = await asyncio.wait_for(session.screenshot(), 15)
            except Exception as exc:
                self.connection_lost(exc)
                raise DeviceError("读取手机画面失败，正在恢复连接。请等待新画面。") from None
            self.last_verified = time.monotonic()
            with Image.open(io.BytesIO(image)) as im:
                im.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                im.save(out, "PNG")
                w, h = im.size
            oid = secrets.token_urlsafe(12)
            now = time.monotonic()
            self.observations = {k: v for k, v in self.observations.items() if now - v[0] < 60}
            if len(self.observations) >= 20:
                del self.observations[next(iter(self.observations))]
            self.observations[oid] = (now, w, h)
            return out.getvalue(), oid, w, h

    async def act(self, action: Action):
        labels = {"tap": "点击", "drag": "滑动", "long_press": "长按", "type": "输入文字",
                  "button": "设备按键", "key": "键盘按键", "launch": "打开 App"}
        if action.kind not in labels:
            raise ValueError("不支持的操作")
        async with self.action_lock:
            session = self.require_session(control=True)
            width = height = 1
            if action.space == "pixels":
                obs = self.observations.get(action.observation_id)
                if not obs or time.monotonic() - obs[0] > 60:
                    raise ValueError("截图已过期或操作后画面已变化，请重新截图")
                _, width, height = obs
            elif action.space != "normalized":
                raise ValueError("不支持的坐标模式")
            point = end = None
            if action.kind in ("tap", "drag", "long_press"):
                point = hid_point(action.x, action.y, width, height)
            if action.kind == "drag":
                end = hid_point(action.x2, action.y2, width, height)
            if action.kind == "type" and action.text is None:
                raise ValueError("缺少输入文字")
            if action.kind == "button" and action.name not in ("home", "lock", "volume-up", "volume-down", "mute"):
                raise ValueError("不支持的设备按键")
            keys = {"enter": 0x28, "backspace": 0x2a, "tab": 0x2b, "escape": 0x29,
                    "left": 0x50, "right": 0x4f, "up": 0x52, "down": 0x51}
            if action.kind == "key" and action.name not in keys:
                raise ValueError("不支持的键盘按键")
            if action.kind == "launch" and not re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+", action.bundle_id or ""):
                raise ValueError("请输入有效的 App 标识")
            # An action invalidates every prior observation, including on partial failure.
            self.observations.clear()
            async def execute():
                async with asyncio.timeout(45):
                    if action.kind == "tap":
                        try:
                            await session.touch("contact", *point)
                            await asyncio.sleep(0.05)
                        finally:
                            await asyncio.wait_for(session.touch("release", *point), 2)
                    elif action.kind in ("drag", "long_press"):
                        current = point
                        try:
                            await session.touch("contact", *point)
                            if action.kind == "long_press":
                                await asyncio.sleep(action.duration)
                            else:
                                steps = max(4, round(action.duration * 40))
                                for i in range(1, steps + 1):
                                    current = tuple(round(a + (b - a) * i / steps) for a, b in zip(point, end))
                                    await session.touch("contact", *current)
                                    await asyncio.sleep(action.duration / steps)
                        finally:
                            await asyncio.wait_for(session.touch("release", *current), 2)
                    elif action.kind == "type":
                        await session.type_text(action.text)
                    elif action.kind == "button":
                        await session.button(action.name)
                    elif action.kind == "key":
                        try:
                            await session.key([keys[action.name]])
                            await asyncio.sleep(0.05)
                        finally:
                            await asyncio.wait_for(session.key([]), 2)
                    elif action.kind == "launch":
                        await session.launch_app(action.bundle_id)
            self.control_task = asyncio.create_task(execute())
            try:
                await self.control_task
            except asyncio.CancelledError:
                self.record(labels[action.kind] + "已中止", False)
                raise DeviceError("操作已中止；请重新观察画面") from None
            except Exception as exc:
                self.record(labels[action.kind] + "未完成", False)
                self.connection_lost(exc)
                raise
            finally:
                self.control_task = None
            self.record(labels[action.kind])
            # A transport acknowledgement does not verify the app's business outcome.
            return {"ok": True, "demo": self.demo, "message": "指令已执行，请重新观察画面确认结果"}


class LocalAccess(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        host = request.headers.get("host", "")
        allowed = {f"127.0.0.1:{request.app.state.port}", f"localhost:{request.app.state.port}"}
        if host not in allowed:
            return JSONResponse({"detail": "不接受此主机名"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://{host}"}:
            return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
        is_api = request.url.path.startswith(("/api/", "/stream/"))
        cookie_name = f"phonepilot_session_{request.app.state.port}"
        key = request.headers.get("x-phonepilot-key") or request.cookies.get(cookie_name, "")
        if is_api and (not key.isascii() or not secrets.compare_digest(key, request.app.state.token)):
            return JSONResponse({"detail": "需要本机会话授权"}, status_code=401)
        response = await call_next(request)
        if request.url.path == "/":
            response.set_cookie(cookie_name, request.app.state.token, httponly=True,
                                samesite="strict", max_age=86400)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return response


def create_app(*, demo=False, port=8765, token=None):
    hub = Hub(demo=demo)

    @asynccontextmanager
    async def lifespan(app):
        app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(20, read=None), trust_env=False)
        try:
            yield
        finally:
            try:
                await hub.disconnect()
                await app.state.http.aclose()
            finally:
                clear_runtime(getattr(app.state, "runtime_path", None))

    app = FastAPI(title="PhonePilot Local", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.hub, app.state.port, app.state.token = hub, port, token or secrets.token_urlsafe(32)
    app.add_middleware(LocalAccess)

    @app.exception_handler(ValueError)
    async def bad_input(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(DeviceError)
    async def device_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Exception)
    async def other_error(request, exc):
        return JSONResponse({"detail": friendly_error(exc)}, status_code=502)

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/static/{name}")
    async def static_file(name: str):
        if name not in ("app.js", "app.css"):
            raise HTTPException(404)
        return FileResponse(STATIC / name)

    @app.get("/api/status")
    async def status():
        return hub.status()

    @app.get("/api/doctor")
    async def doctor(transport: Literal["usb", "wifi"] | None = None):
        return await diagnose(hub, transport)

    @app.get("/api/devices")
    async def devices(transport: Literal["usb", "wifi"] = "usb"):
        return {"devices": await hub.devices(transport), "transport": transport}

    @app.post("/api/connect", status_code=202)
    async def connect(body: Connect):
        await hub.start_connect(body.device_id, body.transport, body.host)
        return hub.status()

    @app.post("/api/prepare-wireless")
    async def prepare_wireless_route(body: Connect):
        async with hub.lifecycle_lock:
            if hub.phase not in ("disconnected", "error"):
                raise ValueError("请先断开控制会话，再准备无线连接")
            hub.setup_busy = True
            try:
                devices = await hub.devices("usb")
                if not any(d["id"] == body.device_id and d.get("trusted") for d in devices):
                    raise ValueError("请先插线，解锁并信任这台 Mac，再选择这部手机")
                if hub.demo:
                    raise ValueError("演示模式不能建立无线配对")
                from .transport import prepare_wireless
                return await asyncio.wait_for(prepare_wireless(body.device_id), 40)
            finally:
                hub.setup_busy = False

    @app.post("/api/disconnect")
    async def disconnect():
        await hub.disconnect()
        return hub.status()

    @app.post("/api/retry", status_code=202)
    async def retry():
        await hub.retry()
        return hub.status()

    @app.post("/api/pause")
    async def pause(body: Pause):
        await hub.pause(body.paused)
        return hub.status()

    @app.post("/api/action")
    async def action(body: Action):
        return await hub.act(body)

    @app.get("/api/screenshot")
    async def screenshot(max_size: int = 1280):
        if not 320 <= max_size <= 4096:
            raise ValueError("截图长边应在 320 到 4096 像素之间")
        png, oid, w, h = await hub.capture(max_size)
        return Response(png, media_type="image/png", headers={"X-Observation-ID": oid,
                        "X-Image-Width": str(w), "X-Image-Height": str(h)})

    @app.get("/api/apps")
    async def apps():
        async with hub.action_lock:
            return {"apps": await hub.require_session().list_apps()}

    @app.get("/api/mcp-config")
    async def mcp_config():
        # stdio client reads a private runtime file; no secret in copied config.
        return {"mcpServers": {"phonepilot": {
            "command": sys.executable, "args": ["-m", "phonepilot.mcp_server"],
            "env": {"PYTHONPATH": str(ROOT), "PHONEPILOT_STATE_DIR": str(app.state.state_dir)}
        }}}

    @app.get("/stream/{path:path}")
    async def stream_proxy(path: str):
        if path not in ("codec", "stream.bin"):
            raise HTTPException(404)
        session = hub.require_session()
        if session.demo:
            raise HTTPException(409, "演示模式使用预览图片")
        auth = base64.b64encode(f"phonepilot:{session.stream_password}".encode()).decode()
        request = app.state.http.build_request("GET", f"http://127.0.0.1:{session.stream_port}/{path}",
                    headers={"Authorization": "Basic " + auth})
        upstream = await app.state.http.send(request, stream=True)
        if upstream.status_code != 200:
            await upstream.aclose()
            raise HTTPException(503, "画面正在准备，请稍候")
        async def chunks():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
        return StreamingResponse(chunks(), media_type=upstream.headers.get("content-type", "application/octet-stream"))

    app.state.state_dir = DEFAULT_STATE.resolve()
    return app


def main():
    parser = argparse.ArgumentParser(description="PhonePilot Local")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--demo", action="store_true", help="Explicit preview; never connects to real hardware")
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    state = args.state_dir.resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    lock = (state / "server.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        parser.exit(1, "PhonePilot 已在运行。请打开原有控制台。\n")
    # Reserve the port before publishing credentials, without touching another server.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", args.port))
        listener.listen(128)
        listener.setblocking(False)
    except PermissionError:
        listener.close()
        parser.exit(1, "当前运行环境不允许监听本地端口，请在本机终端启动。\n")
    except OSError:
        listener.close()
        parser.exit(1, "此本地端口已被使用。请关闭原实例或换一个端口。\n")
    app = create_app(demo=args.demo, port=args.port)
    app.state.state_dir = state
    runtime = state / "runtime.json"
    app.state.runtime_path = runtime
    fd, temporary_name = tempfile.mkstemp(prefix=".runtime-", dir=state)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"url": f"http://127.0.0.1:{args.port}", "token": app.state.token,
                       "pid": os.getpid(), "demo": args.demo}, f)
        temporary.replace(runtime)
    finally:
        temporary.unlink(missing_ok=True)
    import uvicorn
    try:
        config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False,
                                timeout_graceful_shutdown=3)
        asyncio.run(uvicorn.Server(config).serve(sockets=[listener]))
    except KeyboardInterrupt:
        pass
    finally:
        # A second launch must not delete a newer instance's runtime file.
        clear_runtime(runtime)
        listener.close()
        lock.close()


if __name__ == "__main__":
    main()
