from phonepilot import doctor
from phonepilot.server import Hub


def test_command_line_tools_alone_are_not_full_xcode(monkeypatch):
    monkeypatch.setattr(doctor, "command", lambda args: (0, "/Library/Developer/CommandLineTools"))
    checks = {c["id"]: c for c in doctor.installation_checks(dependencies=False)}
    assert not checks["xcode"]["ok"] and not checks["xcode_ready"]["ok"]
    assert "Locations" in checks["xcode"]["hint"]


def test_custom_location_xcode_is_supported(monkeypatch):
    def command(args):
        return (0, "/Applications/Xcode-beta.app/Contents/Developer" if args[0] == "xcode-select" else "Xcode 27.1\nBuild version example")
    monkeypatch.setattr(doctor, "command", command)
    checks = {c["id"]: c for c in doctor.installation_checks(dependencies=False)}
    assert checks["xcode"]["ok"] and checks["xcode_ready"]["ok"]


async def test_device_discovery_is_not_reported_as_connected(monkeypatch):
    monkeypatch.setattr(doctor, "installation_checks", lambda: [])
    hub = Hub(demo=True)
    result = await doctor.diagnose(hub)
    checks = {c["id"]: c for c in result["checks"]}
    assert checks["device"]["ok"] and checks["trust"]["ok"]
    assert not checks["session"]["ok"] and not result["ready"]
