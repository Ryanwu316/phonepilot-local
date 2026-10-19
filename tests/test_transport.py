import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from phonepilot import transport
from phonepilot.native import DeviceError
from phonepilot.server import Hub


@pytest.fixture(autouse=True)
def fresh_discovery_cache(monkeypatch):
    monkeypatch.setattr(transport, "_candidate_cache", {})
    monkeypatch.setattr(transport, "_last_scan", 0)


@pytest.mark.parametrize("host", ["127.0.0.1", "8.8.8.8", "https://192.168.1.2", "::1"])
def test_manual_wireless_address_must_be_local_ipv4(host):
    from phonepilot.server import Connect
    with pytest.raises(ValueError):
        Connect(device_id="chosen", transport="wifi", host=host)
    assert Connect(device_id="chosen", transport="wifi", host="192.168.1.2").host == "192.168.1.2"
    with pytest.raises(ValueError):
        Connect(device_id="chosen", transport="usb", host="192.168.1.2")


@pytest.mark.parametrize("paired,identity", [(False, "chosen"), (True, "other"), (True, "chosen")])
async def test_manual_address_requires_existing_pair_and_selected_identity(monkeypatch, paired, identity):
    calls, closed = [], []
    record = {"synthetic": True}
    async def existing_record(identifier, folder):
        assert identifier == "chosen"
        return record
    monkeypatch.setattr(transport, "get_preferred_pair_record", existing_record)
    class Lockdown:
        udid = identity
        async def close(self):
            closed.append(True)
    lockdown = Lockdown()
    lockdown.paired = paired
    async def create(**kwargs):
        calls.append(kwargs)
        return lockdown
    async def proxy(value):
        assert value is lockdown
        return "provider"
    monkeypatch.setattr(transport, "create_using_tcp", create)
    monkeypatch.setattr(transport.tunnels.CoreDeviceTunnelProxy, "create", proxy)
    if paired and identity == "chosen":
        assert await transport.selected_provider("chosen", "wifi", "192.168.1.2") == ("provider", lockdown)
        assert not closed
    else:
        with pytest.raises(DeviceError, match="不匹配"):
            await transport.selected_provider("chosen", "wifi", "192.168.1.2")
        assert closed == [True]
    assert calls == [{"hostname": "192.168.1.2", "identifier": "chosen", "pair_record": record, "autopair": False, "keep_alive": True}]


@pytest.mark.parametrize("method,expected", [("usb", "USB"), ("wifi", "Network")])
async def test_connection_method_is_explicit_and_never_falls_back_to_usb(monkeypatch, method, expected):
    calls = []
    class Lockdown:
        async def close(self):
            pass
    async def create(**kwargs):
        calls.append(kwargs)
        return Lockdown()
    async def candidates():
        return []
    async def proxy(lockdown):
        return "provider"
    monkeypatch.setattr(transport, "create_using_usbmux", create)
    monkeypatch.setattr(transport, "wireless_candidates", candidates)
    monkeypatch.setattr(transport.tunnels.CoreDeviceTunnelProxy, "create", proxy)
    provider, _ = await transport.selected_provider("chosen", method)
    assert provider == "provider"
    assert calls == [{"serial": "chosen", "connection_type": expected, "autopair": False}]


async def test_wireless_uses_only_selected_paired_device_and_never_autopairs(monkeypatch):
    calls = []
    answer = SimpleNamespace(addresses=[SimpleNamespace(full_ip="192.0.2.1")], port=12345)
    async def candidates():
        return [("other", answer), ("chosen", answer)]
    class Provider:
        def __init__(self, identifier, host, port):
            calls.append((identifier, host, port))
        async def connect(self, autopair):
            assert autopair is False
        async def close(self):
            pass
    monkeypatch.setattr(transport, "wireless_candidates", candidates)
    monkeypatch.setattr(transport.tunnels, "RemotePairingTunnelService", Provider)
    provider, lockdown = await transport.selected_provider("chosen", "wifi")
    assert isinstance(provider, Provider) and lockdown is None
    assert calls == [("chosen", "192.0.2.1", 12345)]


async def test_unknown_bonjour_device_is_not_a_wireless_target(monkeypatch):
    import pymobiledevice3.bonjour as bonjour
    import pymobiledevice3.pair_records as records
    answer = SimpleNamespace(addresses=[SimpleNamespace(full_ip="192.0.2.1")], properties={})
    monkeypatch.setattr(records, "iter_remote_pair_records_by_identifier", lambda: iter([("known", None, {transport.tunnels.PEER_ALT_IRK_KEY: b"key"})]))
    async def browse(timeout):
        return [answer]
    monkeypatch.setattr(bonjour, "browse_remotepairing", browse)
    assert await transport.wireless_candidates() == []


async def test_wireless_recovery_keeps_the_selected_transport(monkeypatch):
    monkeypatch.setattr(Hub, "RECOVERY_DELAYS", (0,))
    hub = Hub(demo=True)
    await hub.start_connect("demo", "wifi")
    await hub.connect_task
    methods = []
    async def devices(method="usb"):
        methods.append(method)
        return [{"id": "demo", "trusted": True}]
    hub.devices = devices
    try:
        hub.connection_lost(ConnectionResetError())
        await hub.recovery_task
        assert hub.phase == "connected" and hub.status()["transport"] == "wifi"
        assert methods == ["wifi"]
    finally:
        await hub.disconnect()


@pytest.mark.parametrize("fail", [False, True])
async def test_explicit_transport_tunnel_releases_singleton_and_resources(monkeypatch, fail):
    userspace = transport.userspace
    closed = []
    gate = asyncio.Event()
    class Client:
        tun = SimpleNamespace(set_peer=lambda value: None)
        async def wait_closed(self):
            await gate.wait()
    class Provider:
        @asynccontextmanager
        async def start_tcp_tunnel(self):
            try:
                yield SimpleNamespace(client=Client(), address="2001:db8::1", port=12345, auxiliary_metadata={})
            finally:
                closed.append("tunnel")
        async def close(self):
            closed.append("provider")
    @asynccontextmanager
    async def plane(*args):
        try:
            yield SimpleNamespace(dial=lambda: None)
        finally:
            closed.append("plane")
    class RSD:
        def __init__(self, *args, **kwargs):
            pass
        async def connect(self):
            if fail:
                raise ConnectionResetError()
        async def close(self):
            closed.append("rsd")
    async def provider(*args):
        return Provider(), None
    monkeypatch.setattr(userspace, "_active_tunnel", None)
    monkeypatch.setattr(userspace, "USERSPACE_ACTIVE", False)
    monkeypatch.setattr(transport.tunnels, "USE_USERSPACE_TUNNEL", False)
    monkeypatch.setattr(transport, "selected_provider", provider)
    monkeypatch.setattr(userspace, "UserspaceDialPlane", plane)
    monkeypatch.setattr(transport, "RemoteServiceDiscoveryService", RSD)
    tunnel = transport.SelectedTransportTunnel("chosen", "wifi")
    if fail:
        with pytest.raises(ConnectionResetError):
            await tunnel.aopen()
    else:
        assert await tunnel.aopen() is tunnel.rsd
        assert userspace.USERSPACE_ACTIVE and userspace._active_tunnel is tunnel
    await tunnel.aclose()
    await asyncio.sleep(0)
    assert not userspace.USERSPACE_ACTIVE and userspace._active_tunnel is None
    assert not transport.tunnels.USE_USERSPACE_TUNNEL
    assert closed == ["rsd", "plane", "tunnel", "provider"]


async def test_initial_connection_never_implicitly_prepares_wireless(monkeypatch):
    from phonepilot.server import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(demo=True), base_url="http://127.0.0.1:8765") as client:
        client.get("/")
        assert client.post("/api/connect", json={"device_id": "demo", "transport": "arbitrary"}).status_code == 422
        assert client.get("/api/devices?transport=arbitrary").status_code == 422
        assert client.post("/api/prepare-wireless", json={"device_id": "demo"}).status_code == 400


@pytest.mark.parametrize("existing", [False, True])
def test_pair_record_has_private_permissions_before_secret_write(tmp_path, monkeypatch, existing):
    path = tmp_path / "pair-record"
    if existing:
        path.write_bytes(b"previous")
        path.chmod(0o644)
    monkeypatch.setattr(transport._PrivatePairingService, "pair_record_path", path)
    def save(self):
        assert path.stat().st_mode & 0o777 == 0o600
        path.write_bytes(b"synthetic-pair-record")
    monkeypatch.setattr(transport.tunnels.RemotePairingLockdownService, "save_pair_record", save)
    service = object.__new__(transport._PrivatePairingService)
    service.save_pair_record()
    assert path.stat().st_mode & 0o777 == 0o600


async def test_absent_wireless_setting_is_reported_as_off():
    from pymobiledevice3.exceptions import MissingValueError
    class Lockdown:
        async def get_enable_wifi_connections(self):
            raise MissingValueError("missing", "synthetic-device", "27.0")
    assert await transport.wifi_enabled(Lockdown()) is False


async def test_manual_address_never_connects_without_existing_record(monkeypatch):
    async def absent(*args):
        return None
    async def forbidden(**kwargs):
        pytest.fail("no TCP connection without selected pairing")
    monkeypatch.setattr(transport, "get_preferred_pair_record", absent)
    monkeypatch.setattr(transport, "create_using_tcp", forbidden)
    with pytest.raises(DeviceError, match="未找到"):
        await transport.selected_provider("chosen", "wifi", "192.168.1.2")

async def test_manual_wireless_recovery_retains_host_without_bonjour(monkeypatch):
    monkeypatch.setattr(Hub, "RECOVERY_DELAYS", (0,))
    hub = Hub(demo=True)
    await hub.start_connect("demo", "wifi", "192.168.1.2")
    await hub.connect_task
    async def forbidden(*args):
        pytest.fail("manual recovery should not depend on discovery")
    hub.devices = forbidden
    try:
        await hub.pause(True)
        hub.connection_lost(ConnectionResetError())
        await hub.recovery_task
        assert hub.phase == "connected" and hub.transport == "wifi"
        assert hub.host == "192.168.1.2" and hub.paused
    finally:
        await hub.disconnect()
