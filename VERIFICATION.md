# Verification

See [docs/compatibility.md](docs/compatibility.md) for the public test scope and
known limits, and [CONTRIBUTING.md](CONTRIBUTING.md) for reproducible commands.

Public documentation images use demo frames. Personal device captures and local
session history are intentionally excluded from the source distribution.

## 2026-10-19 release checks

- 65 Python tests and 16 frontend tests passed again on the release source.
- Frontend formatting and the source-distribution privacy allowlist passed.
- Public preview images were visually checked and contain synthetic demo content.

## 2026-10-18 recovery update

- Clean extracted-source installation with locked dependencies: passed.
- Read-only host checks: passed on the local Mac with Xcode 27.1.
- Automated backend/frontend regression: 65 Python + 16 frontend checks passed.
- Browser UI: missing-device state, failed-connection state, cleared stale frames, and actionable prerequisite checks observed.
- Physical reconnect, paused-state recovery on hardware, long-duration operation:
  pending; simulated regression does not establish these results.
- Physical USB session: fresh HEVC preview, app enumeration/launch, taps and swipes passed.
- Taobao: pending-receipt list and one logistics detail were opened on hardware.
  Complete order/split-package coverage is pending; this is not a completed parcel report.

Wireless preparation succeeded on the test phone. Automatic discovery was intermittent;
a manual private-IPv4 fallback was added with existing-record and device-identity
verification. Existing-pair identity validation passed; the wireless control tunnel then failed during handshake. No successful
cable-free session or physical recovery is claimed.

Wireless selection and a separate network transport have been implemented, including authenticated paired-device discovery and explicit USB-assisted initial pairing. The transport selector and resource cleanup are covered with simulated tests. A USB cable being present during a Wi-Fi test does not prove cable-free operation. Wireless physical acceptance remains pending until the cable is removed and the same phone still provides a fresh screen and working control.
