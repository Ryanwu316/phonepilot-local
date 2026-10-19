"""Read-only installation checks. This module also works before dependencies exist."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=8)
        return result.returncode, result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""


def installation_checks(*, dependencies=True):
    checks = []

    def add(key, name, ok, detail, hint=""):
        checks.append(dict(id=key, name=name, ok=ok, detail=detail, hint=hint if not ok else ""))

    add("mac", "Mac 系统", sys.platform == "darwin", "macOS" if sys.platform == "darwin" else "需要 macOS", "请在 Mac 上运行 PhonePilot。")
    add("python", "Python 环境", sys.version_info >= (3, 11), sys.version.split()[0],
        "重新运行首次安装，准备 Python 3.11 或更新版本。")
    code, selected = command(["xcode-select", "-p"])
    full_xcode = code == 0 and selected.endswith(".app/Contents/Developer")
    add("xcode", "完整 Xcode", full_xcode, "已选择完整 Xcode" if full_xcode else "尚未选择完整 Xcode",
        "安装并打开 Xcode，完成首次准备；在 Xcode 设置的 Locations 中选择 Command Line Tools。")
    code, version = command(["xcodebuild", "-version"]) if full_xcode else (-1, "")
    add("xcode_ready", "Xcode 可用性", code == 0, version.replace("\n", " · ") if code == 0 else "未完成首次准备",
        "打开 Xcode，接受许可并安装它提示的组件，然后重新检查。")
    if dependencies:
        for package, label in (("pymobiledevice3", "手机连接引擎"), ("fastapi", "本地控制台"), ("mcp", "AI 工具接口")):
            try:
                version = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version = None
            add(package, label, bool(version), version or "尚未安装", "重新运行「首次安装.command」。")
    return checks


async def diagnose(hub, transport=None):
    transport = transport or hub.transport
    checks = await asyncio.to_thread(installation_checks)
    devices = []
    discovery_error = None
    try:
        devices = await hub.devices(transport)
    except Exception as exc:
        from .native import friendly_error
        discovery_error = friendly_error(exc)
    visible = any(not device.get("manual_required") for device in devices)
    trusted = any(device.get("trusted") for device in devices)
    if transport == "wifi" and not visible:
        try:
            wired = await hub.devices("usb")
        except Exception:
            wired = []
        if any(device.get("wireless_enabled") is False for device in wired):
            checks.append(dict(id="wireless_permission", name="手机允许无线连接", ok=False,
                               detail="已配对不代表已开启无线接入",
                               hint="手机当前可通过数据线发现，但无线接入尚未开启。选择数据线中的手机，明确点击无线准备按钮。"))
    checks.extend([
        dict(id="device", name="发现 iPhone", ok=visible, detail="已发现设备" if visible else "未发现设备",
             hint="" if visible else discovery_error or ("让手机与 Mac 连接同一 Wi-Fi，先完成一次无线配对，再刷新。" if transport == "wifi" else "连接可传输数据的数据线，解锁手机，再刷新设备列表。")),
        dict(id="trust", name="信任 / 无线配对", ok=trusted, detail="已建立信任或配对" if trusted else "尚未验证",
             hint="" if trusted else ("首次请先选择数据线，选择已信任的手机，再点击无线准备按钮。" if transport == "wifi" else "在 iPhone 上确认信任这台电脑；密码只在手机上输入。")),
        dict(id="session", name="设备连接", ok=hub.phase == "connected",
             detail="设备连接已验证" if hub.phase == "connected" else "等待建立连接",
             hint="" if hub.phase == "connected" else "确认开发者模式已开启，再连接设备；此检查不会自动配对或修改设置。"),
    ])
    return dict(checks=checks, ready=all(check["ok"] for check in checks), demo=hub.demo,
                phase=hub.phase, mirror_required=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--before-install", action="store_true")
    args = parser.parse_args()
    checks = installation_checks(dependencies=not args.before_install)
    if args.json:
        print(json.dumps({"checks": checks, "ready": all(c["ok"] for c in checks)}, ensure_ascii=False))
    else:
        for check in checks:
            print(f"{'✓' if check['ok'] else '○'} {check['name']}：{check['detail']}")
            if check["hint"]:
                print(f"  {check['hint']}")
        print("设备信任与开发者模式将在连接控制台中继续检查。")
    return 0 if all(check["ok"] for check in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
