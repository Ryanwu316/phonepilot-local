#!/usr/bin/env python3
"""Start/check the local hub only. Never connect to or control an iPhone."""
from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class SessionError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward local credentials to a redirect destination.


def project_root(explicit=None):
    value = explicit or os.environ.get("PHONEPILOT_ROOT")
    root = Path(value).expanduser().resolve() if value else Path(__file__).resolve().parents[3]
    if not (root / "phonepilot/server.py").is_file() or not (root / "pyproject.toml").is_file():
        raise SessionError("未找到 PhonePilot 项目。请通过 --root 或 PHONEPILOT_ROOT 指定其目录。")
    return root


def read_runtime(state):
    try:
        config = json.loads((state / "runtime.json").read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        raise SessionError("本机会话文件不可读或已损坏；请检查 PhonePilot 服务状态。") from None
    if not isinstance(config, dict):
        raise SessionError("本机会话格式无效。")
    try:
        parsed = urlsplit(config.get("url", ""))
        valid = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and
                 parsed.port is not None and 1024 <= parsed.port <= 65535 and
                 not any((parsed.username, parsed.password, parsed.path, parsed.query, parsed.fragment)))
    except (ValueError, TypeError, AttributeError):
        valid = False
    token = config.get("token")
    if not valid or not isinstance(token, str) or not token.isascii() or not 16 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise SessionError("本机会话授权信息无效；未向该地址发起请求。")
    return config


def health(state):
    config = read_runtime(state)
    if config is None:
        return {"running": False, "phase": "offline"}
    opener = build_opener(ProxyHandler({}), NoRedirect())
    request = Request(config["url"] + "/api/status", headers={"X-PhonePilot-Key": config["token"]})
    try:
        with opener.open(request, timeout=2) as response:
            data = json.loads(response.read(65536))
    except HTTPError:
        raise SessionError("已有本地服务未接受会话授权；请先检查原服务，不会覆盖或停止它。") from None
    except URLError as exc:
        if isinstance(exc.reason, OSError) and exc.reason.errno in (errno.EPERM, errno.EACCES):
            raise SessionError("当前运行环境禁止访问本机端口；请按工具权限流程在本机运行。") from None
        return {"running": False, "phase": "offline"}
    except (TimeoutError, ConnectionError):
        return {"running": False, "phase": "offline"}
    except ValueError:
        raise SessionError("本地服务返回的状态无法识别。") from None
    if not isinstance(data, dict) or not isinstance(data.get("demo"), bool) or data.get("phase") not in {
        "disconnected", "connecting", "connected", "reconnecting", "disconnecting", "error"
    }:
        raise SessionError("该服务不是可识别的 PhonePilot 控制台。")
    # Deliberate allowlist: no token, UDID, device name, events or input content.
    return {"running": True, "url": config["url"], "version": data.get("version"),
            "demo": data["demo"], "phase": data["phase"], "paused": bool(data.get("paused"))}


def ensure_started(root, state, port=8765, *, spawn=subprocess.Popen):
    current = health(state)
    if current["running"]:
        if current["demo"]:
            raise SessionError("此会话是演示模式，不会当作真机控制台使用。")
        return {**current, "started": False}
    python = root / ".venv/bin/python"
    if not python.is_file():
        raise SessionError("PhonePilot 运行环境未准备好，请先运行项目的「首次安装.command」。")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "launcher.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = health(state)
        if current["running"]:
            if current["demo"]:
                raise SessionError("此会话是演示模式，不会当作真机控制台使用。")
            return {**current, "started": False}
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        process = spawn([str(python), "-m", "phonepilot.server", "--port", str(port), "--state-dir", str(state)],
                        cwd=root, env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True, close_fds=True)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                current = health(state)
                if current["running"] and not current["demo"]:
                    return {**current, "started": False}
                raise SessionError("服务未启动。请检查端口占用、本机端口权限，或使用项目启动文件查看错误。")
            try:
                current = health(state)
            except SessionError:
                # runtime.json may be momentarily incomplete during this process's startup.
                time.sleep(.1)
                continue
            if current["running"]:
                if current["demo"]:
                    raise SessionError("此会话是演示模式，不会当作真机控制台使用。")
                return {**current, "started": True}
            time.sleep(.1)
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        raise SessionError("本地服务启动超时；已结束本次启动的进程。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--state-dir", type=Path, help="Optional isolated state directory")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="Open a healthy console in the default browser")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    try:
        root = project_root(args.root)
        state = args.state_dir.resolve() if args.state_dir else root / "var"
        result = health(state) if args.action == "status" else ensure_started(root, state, args.port)
    except (SessionError, OSError) as exc:
        result = {"running": False, "error": str(exc) if isinstance(exc, SessionError) else "本地环境权限或文件不可用，请检查项目安装。"}
        print(json.dumps(result, ensure_ascii=False))
        return 1
    if args.open and result.get("running"):
        import webbrowser
        webbrowser.open(result["url"])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
