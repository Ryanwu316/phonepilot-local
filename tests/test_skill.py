import importlib.util
import errno
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from phonepilot.server import ROOT


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = load("phonepilot_skill_session", ROOT / "skills/phonepilot/scripts/session.py")
installer = load("phonepilot_skill_install", ROOT / "tools/install_skill.py")


@pytest.mark.parametrize("url", ["https://evil.example:8765", "http://evil.example:8765",
    "http://127.0.0.1:8765@evil.example", "http://user:pass@127.0.0.1:8765",
    "http://127.0.0.1:8765/path", "http://127.0.0.1:8765#fragment", "http://127.0.0.1:1"])
def test_helper_rejects_bad_runtime_urls(tmp_path, url):
    (tmp_path / "runtime.json").write_text(json.dumps({"url": url, "token": "x" * 32}))
    with pytest.raises(helper.SessionError, match="授权信息无效"):
        helper.health(tmp_path)


def test_install_is_idempotent_and_preserves_existing_skill(tmp_path):
    source = ROOT / "skills/phonepilot"
    target, created = installer.install(source, tmp_path)
    assert created and target.is_symlink() and target.resolve() == source
    assert installer.install(source, tmp_path) == (target, False)
    other = tmp_path / "other"
    other.mkdir()
    (other / "SKILL.md").write_text("preserve")
    with pytest.raises(ValueError, match="未覆盖"):
        installer.install(other, tmp_path)
    assert target.resolve() == source


def test_install_preserves_existing_real_skill_directory(tmp_path):
    target = tmp_path / "phonepilot"
    target.mkdir()
    (target / "existing.md").write_text("preserve this")
    with pytest.raises(ValueError, match="未覆盖"):
        installer.install(ROOT / "skills/phonepilot", tmp_path)
    assert not target.is_symlink()
    assert (target / "existing.md").read_text() == "preserve this"


def test_permission_failure_does_not_spawn_another_server(tmp_path, monkeypatch):
    (tmp_path / "runtime.json").write_text(json.dumps({"url": "http://127.0.0.1:8765", "token": "x" * 32}))
    class Blocked:
        def open(self, *args, **kwargs):
            raise helper.URLError(PermissionError(errno.EPERM, "blocked"))
    monkeypatch.setattr(helper, "build_opener", lambda *args: Blocked())
    spawned = []
    with pytest.raises(helper.SessionError, match="运行环境禁止"):
        helper.ensure_started(ROOT, tmp_path, spawn=lambda *args, **kwargs: spawned.append(True))
    assert spawned == []


def test_installed_link_resolves_project_from_another_chat_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("PHONEPILOT_ROOT", raising=False)
    target, _ = installer.install(ROOT / "skills/phonepilot", tmp_path / "user-skills")
    result = subprocess.run([sys.executable, str(target / "scripts/session.py"), "status",
                             "--state-dir", str(tmp_path / "no-session")], cwd=tmp_path,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"running": False, "phase": "offline"}


@pytest.fixture
def local_status_server(tmp_path):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        redirect = False
        def do_GET(self):
            requests.append(self.path)
            if self.redirect:
                self.send_response(302)
                self.send_header("Location", "/must-not-receive-credentials")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = json.dumps({"version": "0.1.0", "demo": False, "phase": "connected",
                "paused": True, "device": {"id": "private-device-id"},
                "events": ["private-content"], "token": "must-not-print"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{http.server_port}"
    (tmp_path / "runtime.json").write_text(json.dumps({"url": url, "token": "x" * 32}))
    try:
        yield tmp_path, Handler, requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=3)


def test_status_redacts_private_fields(local_status_server):
    state, _, _ = local_status_server
    result = helper.health(state)
    assert result["running"] and result["paused"]
    assert set(result) == {"running", "url", "version", "demo", "phase", "paused"}
    assert "private" not in json.dumps(result) and "token" not in result


def test_never_forwards_auth_to_redirect(local_status_server):
    state, handler, requests = local_status_server
    handler.redirect = True
    with pytest.raises(helper.SessionError):
        helper.health(state)
    assert requests == ["/api/status"]


def test_cold_start_then_reuse_without_connecting_device(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    children = []
    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        children.append(process)
        return process
    try:
        first = helper.ensure_started(ROOT, tmp_path, port, spawn=spawn)
        assert first["started"] and not first["demo"] and first["phase"] == "disconnected"
        before = json.loads((tmp_path / "runtime.json").read_text())["pid"]
        second = helper.ensure_started(ROOT, tmp_path, port, spawn=spawn)
        assert not second["started"] and len(children) == 1
        assert json.loads((tmp_path / "runtime.json").read_text())["pid"] == before
    finally:
        for process in children:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
    assert not (tmp_path / "runtime.json").exists()
