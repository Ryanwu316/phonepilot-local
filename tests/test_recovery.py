import asyncio

import pytest

from phonepilot.native import DemoSession, DeviceError
from phonepilot.server import Action, Hub


async def ready(monkeypatch):
    monkeypatch.setattr(Hub, "RECOVERY_DELAYS", (0, 0, 0))
    monkeypatch.setattr(Hub, "DEVICE_POLL_INTERVAL", .005)
    monkeypatch.setattr(Hub, "DEVICE_RETURN_WINDOW", .05)
    hub = Hub(demo=True)
    await hub.start_connect("demo")
    await hub.connect_task
    return hub


async def test_recovery_invalidates_observation_preserves_pause_and_never_replays(monkeypatch):
    hub = await ready(monkeypatch)
    try:
        _, oid, _, _ = await hub.capture()
        original, epoch = hub.session, hub.session_epoch
        await hub.pause(True)
        hub.connection_lost(ConnectionResetError())
        assert hub.phase == "reconnecting" and not hub.observations
        with pytest.raises(DeviceError, match="正在恢复"):
            await hub.act(Action(kind="button", name="home"))
        await hub.recovery_task
        assert hub.phase == "connected" and hub.session is not original
        assert hub.paused and hub.session_epoch > epoch
        assert hub.session.actions == original.actions == []
        await hub.pause(False)
        with pytest.raises(ValueError, match="截图已过期"):
            await hub.act(Action(kind="tap", x=1, y=1, space="pixels", observation_id=oid))
    finally:
        await hub.disconnect()


async def test_failed_input_is_not_resent_on_new_connection(monkeypatch):
    hub = await ready(monkeypatch)
    original = hub.session
    async def uncertain(text):
        original.actions.append(("partial-input",))
        raise ConnectionResetError("closed")
    original.type_text = uncertain
    try:
        with pytest.raises(ConnectionResetError):
            await hub.act(Action(kind="type", text="sensitive input"))
        await hub.recovery_task
        assert hub.phase == "connected"
        assert original.actions == [("partial-input",)]
        assert hub.session.actions == []
        assert "sensitive input" not in str(hub.status())
    finally:
        await hub.disconnect()


async def test_recovery_waits_for_same_phone_ignores_another(monkeypatch):
    hub = await ready(monkeypatch)
    polls = 0
    async def devices(transport="usb"):
        nonlocal polls
        polls += 1
        rows = [{"id": "another", "trusted": True}]
        if polls > 2:
            rows.append({"id": "demo", "trusted": True})
        return rows
    hub.devices = devices
    try:
        hub.connection_lost(ConnectionResetError())
        await hub.recovery_task
        assert hub.phase == "connected" and hub.device["id"] == "demo"
        assert polls == 3
    finally:
        await hub.disconnect()


async def test_absent_phone_stops_recovery_and_keeps_retry_target(monkeypatch):
    hub = await ready(monkeypatch)
    async def devices(transport="usb"):
        return []
    hub.devices = devices
    try:
        hub.connection_lost(ConnectionResetError())
        await hub.recovery_task
        assert hub.phase == "error" and hub.session is None
        assert hub.recovery_stage == "stopped" and hub.device["id"] == "demo"
        with pytest.raises(ValueError):
            await hub.retry()
        assert hub.phase == "error" and hub.device["id"] == "demo"
    finally:
        await hub.disconnect()


async def test_reconnect_attempts_are_bounded(monkeypatch):
    hub = await ready(monkeypatch)
    calls = 0
    async def broken(self, device_id):
        nonlocal calls
        calls += 1
        raise TimeoutError()
    monkeypatch.setattr(DemoSession, "connect", broken)
    try:
        hub.connection_lost(ConnectionResetError())
        await hub.recovery_task
        assert calls == 3 and hub.phase == "error" and hub.session is None
        assert hub.recovery_stage == "stopped"
    finally:
        await hub.disconnect()


async def test_disconnect_cancels_recovery_and_does_not_resurrect_session(monkeypatch):
    hub = await ready(monkeypatch)
    connecting = asyncio.Event()
    async def stuck(self, device_id):
        connecting.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(DemoSession, "connect", stuck)
    hub.connection_lost(ConnectionResetError())
    await connecting.wait()
    recovery = hub.recovery_task
    await asyncio.wait_for(hub.disconnect(), 1)
    assert recovery.done() and hub.phase == "disconnected" and not hub.session
    assert hub.monitor_task is None and hub.pending is None and not hub.auto_recover


async def test_health_monitor_detects_loss_without_any_browser(monkeypatch):
    monkeypatch.setattr(Hub, "HEALTH_INTERVAL", .005)
    hub = await ready(monkeypatch)
    async def broken():
        raise ConnectionResetError()
    hub.session.screenshot = broken
    try:
        async with asyncio.timeout(2):
            while hub.recovery_task is None:
                await asyncio.sleep(.005)
            await hub.recovery_task
        assert hub.phase == "connected" and hub.last_verified is not None
    finally:
        await hub.disconnect()


async def test_duplicate_connect_reuses_session(monkeypatch):
    hub = await ready(monkeypatch)
    try:
        session, epoch = hub.session, hub.session_epoch
        await hub.start_connect("demo")
        assert hub.session is session and hub.session_epoch == epoch
    finally:
        await hub.disconnect()
