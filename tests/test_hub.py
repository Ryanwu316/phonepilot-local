import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from phonepilot.native import DemoSession, DeviceError
from phonepilot.server import Action, Hub, create_app, hid_point


@pytest.fixture
def client():
    app = create_app(demo=True, token="test-token")
    with TestClient(app, base_url="http://127.0.0.1:8765") as value:
        yield value


def test_local_access_boundaries(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"X-PhonePilot-Key": "bad"}).status_code == 401
    assert client.get("/api/status", headers={"X-PhonePilot-Key": "test-token"}).status_code == 200
    assert client.get("/", headers={"Host": "attacker.example:8765"}).status_code == 403
    assert client.get("/", headers={"Host": "testserver"}).status_code == 403
    assert client.get("/", headers={"Origin": "https://attacker.example"}).status_code == 403
    page = client.get("/")
    assert "HttpOnly" in page.headers["set-cookie"]
    assert "SameSite=strict" in page.headers["set-cookie"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert client.get("/api/status").status_code == 200
    assert client.post("/api/pause", json={"paused": True}, headers={"Origin": "null"}).status_code == 403
    assert client.post("/api/action", json={"kind": "exec", "text": "ignored"}).status_code == 400


def test_routes_and_body_validation(client):
    client.get("/")
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/secret").status_code == 404
    assert client.post("/api/connect", json={"device_id": "not-listed"}).status_code == 400
    assert client.post("/api/connect", json={"device_id": "demo", "autopair": True}).status_code == 422
    assert client.post("/api/pause", json={"paused": True}).status_code == 200
    assert client.get("/api/screenshot").status_code == 409
    config = client.get("/api/mcp-config").json()
    assert "test-token" not in str(config)
    assert "phonepilot.mcp_server" in str(config)


def test_multiple_local_ports_have_independent_cookies():
    # Browsers share host cookies across ports. Demo must not overwrite real-device auth.
    a = create_app(demo=True, port=8765, token="one")
    b = create_app(demo=True, port=8766, token="two")
    with TestClient(a, base_url="http://127.0.0.1:8765") as first, TestClient(b, base_url="http://127.0.0.1:8766") as second:
        first.get("/")
        second.cookies.update(first.cookies)
        second.get("/")
        first.cookies.update(second.cookies)
        assert first.get("/api/status").status_code == 200
        assert second.get("/api/status").status_code == 200


async def connected_hub():
    hub = Hub(demo=True)
    await hub.start_connect("demo")
    await hub.connect_task
    return hub


async def test_scaled_screenshot_and_consumed_observations():
    hub = await connected_hub()
    try:
        png, oid, width, height = await hub.capture(640)
        assert png.startswith(b"\x89PNG")
        assert (width, height) == (296, 640)
        await hub.act(Action(kind="tap", x=width / 2, y=height / 2, space="pixels", observation_id=oid))
        assert hub.session.actions == [("touch", "contact", 32768, 32768), ("touch", "release", 32768, 32768)]
        with pytest.raises(ValueError, match="截图已过期"):
            await hub.act(Action(kind="tap", x=1, y=1, space="pixels", observation_id=oid))
        _, oid, w, h = await hub.capture()
        with pytest.raises(ValueError, match="超出"):
            await hub.act(Action(kind="tap", x=w, y=1, space="pixels", observation_id=oid))
        assert oid in hub.observations  # Invalid input never consumes a valid observation.
        hub.observations[oid] = (time.monotonic() - 61, w, h)
        with pytest.raises(ValueError, match="截图已过期"):
            await hub.act(Action(kind="tap", x=1, y=1, space="pixels", observation_id=oid))
    finally:
        await hub.disconnect()


@pytest.mark.parametrize("point", [(-1, 0), (0, -1), (1, .5), (.5, 1), (float("nan"), 0), (float("inf"), 0)])
def test_reject_invalid_coordinates(point):
    with pytest.raises(ValueError):
        hid_point(*point)


async def test_pause_interrupts_gesture_and_releases_contact():
    hub = await connected_hub()
    try:
        action = asyncio.create_task(hub.act(Action(kind="long_press", x=.5, y=.5, duration=3)))
        while not hub.session.actions:
            await asyncio.sleep(.001)
        await asyncio.wait_for(hub.pause(True), 1)
        with pytest.raises(DeviceError, match="已中止"):
            await action
        assert hub.session.actions[-1] == ("touch", "release", 32768, 32768)
        count = len(hub.session.actions)
        with pytest.raises(DeviceError, match="暂停"):
            await hub.act(Action(kind="button", name="home"))
        assert len(hub.session.actions) == count
        await hub.capture()  # Paused control still allows observation.
        await hub.pause(False)
        await hub.act(Action(kind="button", name="home"))
    finally:
        await hub.disconnect()


async def test_pause_interrupts_typing_with_no_content_in_events():
    hub = await connected_hub()
    started, released = asyncio.Event(), asyncio.Event()
    async def long_typing(text):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            released.set()
    hub.session.type_text = long_typing
    action = asyncio.create_task(hub.act(Action(kind="type", text="private text")))
    await started.wait()
    await hub.pause(True)
    with pytest.raises(DeviceError):
        await action
    assert released.is_set()
    assert "private text" not in str(hub.status())
    await hub.disconnect()


async def test_disconnect_during_connect_and_reconnect(monkeypatch):
    started, closed = asyncio.Event(), asyncio.Event()
    original = DemoSession.connect
    async def slow(self, device_id):
        started.set()
        await asyncio.sleep(60)
    async def close(self):
        closed.set()
    monkeypatch.setattr(DemoSession, "connect", slow)
    monkeypatch.setattr(DemoSession, "close", close)
    hub = Hub(demo=True)
    await hub.start_connect("demo")
    await started.wait()
    await asyncio.wait_for(hub.disconnect(), 1)
    assert closed.is_set() and hub.phase == "disconnected" and hub.session is None
    monkeypatch.setattr(DemoSession, "connect", original)
    await hub.start_connect("demo")
    await hub.connect_task
    assert hub.phase == "connected"
    await hub.disconnect()


async def test_connection_failure_never_reports_connected(monkeypatch):
    async def broken(self, device_id):
        raise TimeoutError()
    monkeypatch.setattr(DemoSession, "connect", broken)
    hub = Hub(demo=True)
    await hub.start_connect("demo")
    await hub.connect_task
    assert hub.phase == "error" and hub.session is None
    assert "超时" in hub.error
    await hub.disconnect()
