"""Explicit USB / Wi-Fi selection over the pinned upstream userspace tunnel.

The upstream handle owns cleanup and its process-wide PyTCP singleton. Its provider
factory currently prefers usbmux even when Wi-Fi is requested, so this subclass
keeps the same lifecycle but chooses a provider explicitly. No global function is
monkeypatched. Keep this adapter pinned and test it when updating upstream.
"""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import os
from time import monotonic

from pymobiledevice3.lockdown import create_using_usbmux, create_using_tcp
from pymobiledevice3.pair_records import get_preferred_pair_record, get_home_folder
from pymobiledevice3.remote import tunnel_service as tunnels
from pymobiledevice3.remote import userspace_tunnel as userspace
from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService

from .native import DeviceError

_candidate_cache = {}
_last_scan = 0
WIRELESS_DISCOVERY_TTL = 120


async def wireless_candidates(timeout=2):
    global _last_scan
    from pymobiledevice3.bonjour import browse_remotepairing
    from pymobiledevice3.pair_records import iter_remote_pair_records_by_identifier
    records = {identifier: record[tunnels.PEER_ALT_IRK_KEY]
               for identifier, _, record in iter_remote_pair_records_by_identifier()
               if tunnels.PEER_ALT_IRK_KEY in record}
    if not records:
        _candidate_cache.clear()
        return []
    now = monotonic()
    for identifier in list(_candidate_cache):
        if identifier not in records or now - _candidate_cache[identifier][0] > WIRELESS_DISCOVERY_TTL:
            del _candidate_cache[identifier]
    if now - _last_scan < 3:
        return [(identifier, value[1]) for identifier, value in _candidate_cache.items()]
    answers = await browse_remotepairing(timeout=timeout)
    _last_scan = monotonic()
    for answer in answers:
        identifier = tunnels._match_remote_pair_record(answer, records)
        if identifier and answer.addresses:
            _candidate_cache[identifier] = (_last_scan, answer)
    # mDNS may miss one short browse. A brief cache prevents list flapping;
    # pair-verify and the first screenshot still establish actual availability.
    return [(identifier, value[1]) for identifier, value in _candidate_cache.items()]


async def selected_provider(device_id, transport, host=None):
    if transport == "wifi" and host:
        # TcpLockdownClient replaces identifier with hostname. Supply the selected
        # device's existing record explicitly so it never looks up an IP record.
        record = await get_preferred_pair_record(device_id, get_home_folder())
        if record is None:
            raise DeviceError("未找到这部手机的已信任配对，请先插线完成无线准备。")
        lockdown = await create_using_tcp(hostname=host, identifier=device_id, pair_record=record,
                                          autopair=False, keep_alive=True)
        try:
            if not lockdown.paired or lockdown.udid != device_id:
                raise DeviceError("该地址的手机与所选配对不匹配，请检查手机 IP。")
            return await tunnels.CoreDeviceTunnelProxy.create(lockdown), lockdown
        except BaseException:
            await lockdown.close()
            raise
    if transport == "wifi":
        for identifier, answer in await wireless_candidates():
            if identifier != device_id:
                continue
            for address in answer.addresses:
                provider = tunnels.RemotePairingTunnelService(identifier, address.full_ip, answer.port)
                try:
                    await asyncio.wait_for(provider.connect(autopair=False), 8)
                    return provider, None
                except BaseException as exc:
                    await provider.close()
                    if isinstance(exc, asyncio.CancelledError):
                        raise
        connection_type = "Network"
    else:
        connection_type = "USB"
    lockdown = await create_using_usbmux(serial=device_id, connection_type=connection_type, autopair=False)
    try:
        return await tunnels.CoreDeviceTunnelProxy.create(lockdown), lockdown
    except BaseException:
        await lockdown.close()
        raise


class SelectedTransportTunnel(userspace.UserspaceRsdTunnel):
    def __init__(self, device_id, transport, host=None):
        super().__init__(serial=device_id, autopair=False, remotepairing_fallback=False)
        self.transport = transport
        self.host = host

    async def _aopen_locked(self):
        if userspace._active_tunnel is not None:
            raise DeviceError("原设备连接尚未释放，请等待清理完成后重试。")
        tunnels.USE_USERSPACE_TUNNEL = True
        resources = AsyncExitStack()
        try:
            provider, lockdown = await selected_provider(self.serial, self.transport, self.host)
            resources.push_async_callback(provider.close)
            if lockdown is not None:
                resources.push_async_callback(lockdown.close)
            result = await resources.enter_async_context(provider.start_tcp_tunnel())
            self.tun = result.client.tun
            self.tun.set_peer(result.address)
            plane = await resources.enter_async_context(userspace.UserspaceDialPlane(self.tun, result.address))
            rsd = RemoteServiceDiscoveryService((result.address, result.port), open_connection=plane.dial,
                                               auxiliary_metadata=result.auxiliary_metadata)
            resources.push_async_callback(rsd.close)
            await rsd.connect()
        except BaseException:
            try:
                await resources.aclose()
            finally:
                tunnels.USE_USERSPACE_TUNNEL = False
                self.tun = None
            raise
        self._exit_stack, self.rsd = resources, rsd
        userspace._active_tunnel, userspace.USERSPACE_ACTIVE = self, True
        self._transport_watcher = asyncio.create_task(self._watch_transport_closed(result.client))
        return rsd


class _PrivatePairingService(tunnels.RemotePairingLockdownService):
    def save_pair_record(self):
        # Create with owner-only permissions before upstream writes secret keys.
        fd = os.open(self.pair_record_path, os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        super().save_pair_record()


async def wifi_enabled(lockdown):
    from pymobiledevice3.exceptions import MissingValueError
    try:
        return await lockdown.get_enable_wifi_connections()
    except MissingValueError:
        return False  # A default/off key can be absent on an already-paired phone.


async def prepare_wireless(device_id):
    """Explicit one-time RemotePairing setup over a previously trusted USB link."""
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    lockdown = await create_using_usbmux(serial=device_id, connection_type="USB", autopair=False)
    service = None
    try:
        service = await _PrivatePairingService.create(lockdown)
        try:
            await asyncio.wait_for(service.connect(autopair=True), 30)
        except RemotePairingCompletedError:
            pass  # The phone closes the channel after a successful pair-setup.
        path = service.pair_record_path
        if not path.is_file():
            raise DeviceError("无线配对记录尚未生成，请检查手机的开发者模式和信任状态。")
        os.chmod(path, 0o600)
        # Existing trust alone does not enable Wi-Fi lockdown advertisements.
        # This setting changes only on this explicit preparation action.
        if not await wifi_enabled(lockdown):
            await lockdown.set_enable_wifi_connections(True)
        if not await wifi_enabled(lockdown):
            raise DeviceError("配对已保存，但手机尚未允许无线连接。请在设备中心检查无线连接设置。")
        return {"ok": True, "message": "已准备本机配对并允许无线连接。保持手机与 Mac 在同一 Wi-Fi，选择无线并刷新；拔线后仍能连接才算验证通过。"}
    finally:
        if service is not None:
            await service.close()
        await lockdown.close()
