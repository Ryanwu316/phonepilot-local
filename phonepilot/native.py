"""Adapter for the pinned, GPL-3.0 pymobiledevice3 implementation.

We reuse its CoreDevice stream/HID implementation, not Apple's Mirroring UI.
The stream lifecycle below follows ScreenStreamServer.serve(), with our own
lifespan instead of its standalone CLI signal handlers. Keep the dependency
pinned; these lifecycle hooks are private upstream APIs.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import secrets
from typing import Any

from PIL import Image, ImageDraw


class DeviceError(RuntimeError):
    pass


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, DeviceError):
        return str(exc)
    name = type(exc).__name__.lower()
    raw = str(exc).lower()
    if "passwordprotected" in name + raw or "locked" in name + raw:
        return "iPhone 已锁定，请在手机上手动解锁；本应用不会输入锁屏密码。"
    if any(x in name + raw for x in ("connectionreset", "connectionaborted", "connectionterminated", "brokenpipe", "connection closed", "no device", "devicenotfound", "connectionlost")):
        return "与 iPhone 的连接已中断。请检查数据线或同一 Wi-Fi，并保持原手机解锁；连接恢复后重新确认画面。"
    if any(x in name + raw for x in ("pairing", "notpaired", "passwordprotected", "userdenied", "not trusted")):
        return "请解锁 iPhone，并在手机上确认信任这台 Mac；本应用不会自动配对。"
    if "developer" in raw or "ddi" in raw or "service" in name or "cryptex" in raw:
        return "设备开发者服务未就绪。请先在 Xcode 设备中心完成配对，开启手机开发者模式，并准备开发者支持组件。"
    if "camera" in raw or "microphone" in raw or "media in use" in raw:
        return "手机的相机或麦克风正在使用中。请结束相关 App 的录制后重新连接。"
    if isinstance(exc, TimeoutError):
        return "连接设备超时。请确认手机已解锁、数据线正常，且没有其他软件正在控制同一台手机。"
    return f"设备连接未完成（{type(exc).__name__}）。请检查数据线、信任状态、开发者模式和 Xcode 设备支持。"


async def discover_devices(transport="usb") -> list[dict[str, Any]]:
    from pymobiledevice3.usbmux import list_devices
    from pymobiledevice3.lockdown import create_using_usbmux

    result = []
    # A paired device may appear over both USB and the network. Prefer USB,
    # never present duplicate entries for the same physical phone.
    devices = sorted(await list_devices(), key=lambda d: str(d.connection_type).lower() != "usb")
    seen = set()
    for device in devices:
        if str(device.connection_type).lower() != ("usb" if transport == "usb" else "network"):
            continue
        if device.serial in seen:
            continue
        seen.add(device.serial)
        row = {"id": device.serial, "name": "iPhone", "transport": device.connection_type,
               "trusted": False, "version": None}
        client = None
        try:
            client = await asyncio.wait_for(create_using_usbmux(serial=device.serial, connection_type=device.connection_type, autopair=False), 4)
            row.update(name=client.all_values.get("DeviceName", "iPhone"),
                       version=client.all_values.get("ProductVersion"), trusted=True)
            if transport == "usb":
                from .transport import wifi_enabled
                with contextlib.suppress(Exception):
                    row["wireless_enabled"] = await asyncio.wait_for(wifi_enabled(client), 2)
        except Exception as exc:
            row["hint"] = friendly_error(exc)
        finally:
            if client is not None:
                await client.close()
        result.append(row)
    if transport == "wifi":
        from .transport import wireless_candidates
        from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier
        for identifier, _ in await wireless_candidates():
            if not any(row["id"] == identifier for row in result):
                result.append({"id": identifier, "name": "已配对 iPhone", "transport": "Wi-Fi",
                               "trusted": True, "version": None})
        for identifier, _, _ in iter_remote_pair_records_by_identifier():
            if not any(row["id"] == identifier for row in result):
                result.append({"id": identifier, "name": "已配对 iPhone（手动地址）", "transport": "Wi-Fi",
                               "trusted": True, "version": None, "manual_required": True})
    return result


class NativeSession:
    demo = False

    def __init__(self, transport="usb", host=None) -> None:
        self.transport = transport
        self.host = host
        self.tunnel = None
        self.rsd = None
        self.stream = None
        self.listener = None
        self.tasks: list[asyncio.Task] = []
        self.stream_port = None
        self.stream_password = secrets.token_urlsafe(32)
        self.width = self.height = 0

    async def connect(self, device_id: str) -> None:
        from .transport import SelectedTransportTunnel
        from pymobiledevice3.remote.core_device.screen_stream import ScreenStreamServer

        # Existing trust only. Never enable Developer Mode or pair implicitly.
        self.tunnel = SelectedTransportTunnel(device_id, self.transport, self.host)
        try:
            self.rsd = await asyncio.wait_for(self.tunnel.aopen(), 30)
            await self.screenshot()
            self.stream = ScreenStreamServer(self.rsd, bind="127.0.0.1", http_port=0,
                                             password=self.stream_password, audio_default_on=False)
            # Same preflight as upstream; camera/microphone conflicts must fail visibly.
            await asyncio.wait_for(self.stream._eager_stream_start(), 30)
            self.listener = await asyncio.start_server(self.stream._handle_http, "127.0.0.1", 0)
            self.stream_port = self.listener.sockets[0].getsockname()[1]
            for method in ("_stall_watchdog", "_decoder_refresh_loop", "_rctl_feedback_loop",
                           "_keep_awake_loop", "_hid_worker"):
                task = asyncio.create_task(getattr(self.stream, method)(), name=f"phonepilot{method}")
                self.tasks.append(task)
                # The upstream HID/reconnect helpers refer to these task attributes.
                attr = {"_hid_worker": "_hid_worker_task", "_rctl_feedback_loop": "_rctl_task",
                        "_keep_awake_loop": "_keep_awake_task"}.get(method)
                if attr:
                    setattr(self.stream, attr, task)
        except BaseException:
            await self.close()
            raise

    async def screenshot(self) -> bytes:
        from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService
        if self.rsd is None:
            raise DeviceError("请先连接 iPhone")
        async with ScreenCaptureService(self.rsd) as service:
            response = await asyncio.wait_for(service.capture_screenshot(), 12)
        image = bytes(response["image"])
        with Image.open(io.BytesIO(image)) as im:
            self.width, self.height = im.size
        return image

    async def touch(self, kind: str, x: int, y: int) -> None:
        if not self.stream:
            raise DeviceError("设备流未就绪")
        code, body = await self.stream._handle_touch(json.dumps({"type": kind, "x": x, "y": y}).encode())
        if code != 200:
            raise DeviceError(body.decode(errors="replace"))

    async def key(self, usages: list[int]) -> None:
        if not self.stream:
            raise DeviceError("设备流未就绪")
        code, body = await self.stream._handle_key(json.dumps({"usages": usages}).encode())
        if code != 200:
            raise DeviceError(body.decode(errors="replace"))

    async def button(self, name: str) -> None:
        if not self.stream:
            raise DeviceError("设备流未就绪")
        code, body = await self.stream._handle_button(json.dumps({"name": name, "state": "press"}).encode())
        if code != 200:
            raise DeviceError(body.decode(errors="replace"))

    async def type_text(self, text: str) -> None:
        from pymobiledevice3.remote.core_device.hid_service import ASCII_TO_HID, KEY_LEFT_SHIFT, KEY_LEFT_GUI
        if any(c not in ASCII_TO_HID for c in text):
            # The device pasteboard is used only on explicit text-entry requests.
            # Never read the user's existing pasteboard or log typed text.
            await self.stream._clipboard_set(text=text)
            try:
                await self.key([KEY_LEFT_GUI])
                await asyncio.sleep(0.08)
                await self.key([KEY_LEFT_GUI, 0x19])  # Command-V
                await asyncio.sleep(0.08)
            finally:
                await asyncio.wait_for(self.key([]), 2)
            return
        try:
            for char in text:
                usage, shift = ASCII_TO_HID[char]
                if shift:
                    await self.key([KEY_LEFT_SHIFT])
                    await asyncio.sleep(0.025)
                await self.key(([KEY_LEFT_SHIFT] if shift else []) + [usage])
                await asyncio.sleep(0.025)
                await self.key([])
                await asyncio.sleep(0.015)
        finally:
            await asyncio.wait_for(self.key([]), 2)

    async def list_apps(self) -> Any:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        async with AppServiceService(self.rsd) as service:
            return await asyncio.wait_for(service.list_apps(include_hidden_apps=False, include_internal_apps=False), 15)

    async def launch_app(self, bundle_id: str) -> Any:
        from pymobiledevice3.remote.core_device.app_service import AppServiceService
        async with AppServiceService(self.rsd) as service:
            return await asyncio.wait_for(service.launch_application(bundle_id, kill_existing=False), 15)

    async def close(self) -> None:
        async def bounded(fn, seconds=3):
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(fn(), seconds)

        if self.listener:
            self.listener.close()
            await bounded(self.listener.wait_closed)
        stream = self.stream
        if stream:
            await asyncio.sleep(0)
            handlers = [t for t in stream._http_tasks if not t.done()]
            for task in [*handlers, *self.tasks, *stream._pli_tasks]:
                task.cancel()
            if handlers or self.tasks:
                await asyncio.wait([*handlers, *self.tasks], timeout=2)
            for method in ("_stop_hid", "_stop_active_stream", "_stop_audio_stream",
                           "_stop_clipboard_monitor", "_stop_accessibility"):
                await bounded(getattr(stream, method))
            if self.rsd:
                # The pinned upstream closes local readers above but requires
                # this RPC to release the device's remote-observation assertion.
                # Do not run another CoreDevice mirror against the same phone.
                from pymobiledevice3.remote.core_device.display_service import DisplayService
                await bounded(lambda: DisplayService.stop_all_streams(self.rsd), 6)
        if self.tunnel:
            await bounded(self.tunnel.aclose, 6)
        self.stream = self.listener = self.rsd = self.tunnel = None
        self.tasks = []
        self.stream_port = None


class DemoSession:
    """Explicit UI/test preview; never enumerates or connects to real hardware."""
    demo = True
    width, height = 390, 844
    stream_port = None
    stream_password = ""

    def __init__(self):
        self.actions: list[tuple] = []
        self.title = "Settings"

    async def connect(self, device_id):
        await asyncio.sleep(0.1)

    async def close(self):
        pass

    async def screenshot(self):
        im = Image.new("RGB", (390, 844), "#f2f2f7")
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((145, 12, 245, 43), radius=16, fill="#101115")
        draw.text((25, 23), "9:41", fill="#17181b", font_size=16)
        draw.text((25, 93), self.title, fill="#17181b", font_size=34)
        draw.rounded_rectangle((18, 155, 372, 220), radius=14, fill="white")
        draw.text((36, 174), "PhonePilot Preview", fill="#17181b", font_size=19)
        draw.text((36, 199), "No real iPhone is connected", fill="#747982", font_size=12)
        rows = [("Airplane Mode", "#ed8c3b"), ("Wi-Fi", "#3478f6"), ("Bluetooth", "#3478f6"),
                ("Notifications", "#ee5365"), ("Sounds & Haptics", "#ee5365"),
                ("Focus", "#7266cf"), ("Screen Time", "#7266cf"), ("General", "#8e939d")]
        for i, (label, color) in enumerate(rows):
            y = 248 + i * 56
            draw.rounded_rectangle((18, y, 372, y + 52), radius=9, fill="white")
            draw.rounded_rectangle((31, y + 11, 61, y + 41), radius=7, fill=color)
            draw.text((78, y + 17), label, fill="#25262a", font_size=16)
            draw.text((346, y + 16), ">", fill="#b7bac3", font_size=17)
        draw.text((62, 762), "DEMO / NO DEVICE ACTIONS", fill="#747982", font_size=15)
        draw.rounded_rectangle((135, 824, 255, 829), radius=3, fill="#15171a")
        out = io.BytesIO()
        im.save(out, "PNG")
        return out.getvalue()

    async def touch(self, kind, x, y):
        self.actions.append(("touch", kind, x, y))

    async def key(self, usages):
        self.actions.append(("key", usages))

    async def button(self, name):
        self.actions.append(("button", name))

    async def type_text(self, text):
        self.actions.append(("type", len(text)))

    async def list_apps(self):
        return [{"bundleIdentifier": "com.apple.Preferences", "name": "Settings"},
                {"bundleIdentifier": "com.apple.mobiletimer", "name": "Clock"}]

    async def launch_app(self, bundle_id):
        self.title = "Clock" if bundle_id == "com.apple.mobiletimer" else "Settings"
        self.actions.append(("launch", bundle_id))
        return {"ok": True, "demo": True}
