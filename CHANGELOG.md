# Changelog

## 0.1.0 — 2026-10-19 (experimental)

- macOS local console with a CoreDevice adapter pinned to pymobiledevice3.
- Live HEVC preview, periodic-screenshot fallback, and an adaptive iPhone-style frame.
- Manual input, app launching, screenshots, canvas recording, and pause/resume.
- Ten MCP tools and an installable agent skill sharing the console session.
- Installation checks, a reusable background launcher, bounded same-device recovery,
  preserved pause, stale-frame removal, and no replay of uncertain input.
- Explicit USB/Wi-Fi selection, opt-in wireless preparation, and a manual private-IP
  connection path. Wireless control has not passed physical acceptance.
- A read-only Taobao parcel workflow covering split shipments and incomplete results.
- Chinese/English documentation, demo-only public previews, and source packaging
  excluding device sessions, pairing records, and private captures.

Local validation: 65 Python tests and 16 frontend tests passed. USB preview and
basic control were observed on one physical-device configuration. Wireless tunnel
establishment failed in the recorded test; physical disconnect recovery, complete
parcel coverage, non-ASCII input, recording, and long-duration stability remain
unverified. See [the verification record](VERIFICATION.md).
