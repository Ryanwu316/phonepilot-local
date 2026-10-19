# Third-party notices

PhonePilot Local is an independent project licensed under GPL-3.0-or-later.

The iPhone transport, screen stream and HID implementation comes from
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3), copyright its
contributors, licensed under GPL-3.0. The pinned revision is
`d1e971b6fbf84a35c7af8195e4d801f3689658c6` (11.33.1).

`phonepilot/transport.py` adapts the provider selection and lifecycle of
`pymobiledevice3/remote/userspace_tunnel.py`, preserving its singleton guard and cleanup.
`phonepilot/native.py` adapts the lifecycle of
`pymobiledevice3/remote/core_device/screen_stream.py` to the local hub. The stream
reader in `static/app.js` implements the framing documented by that upstream
server and viewer. See the pinned upstream source for the original implementations
and contributor history. The source dependency is available via the immutable Git
revision specified in pyproject.toml and uv.lock.

Other dependencies include FastAPI (MIT), Starlette (BSD-3-Clause), Uvicorn
(BSD-3-Clause), HTTPX (BSD-3-Clause), MCP Python SDK (MIT), Pillow (HPND), and their
transitive dependencies. Each dependency's own license remains applicable; installed
license notices are included in its package metadata in `.venv`.

Apple, iPhone, macOS and Xcode are trademarks of Apple Inc. OpenAI product names
belong to OpenAI. This project is not affiliated with or endorsed by Apple or OpenAI.
