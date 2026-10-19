# PhonePilot Local

**Your iPhone, beside your AI conversation.**

A local iPhone console for macOS: live screen preview, mouse and keyboard control,
and MCP tools that reuse the same device session. Includes a reusable agent skill.
No PhonePilot account or additional model API key is required.

[简体中文](README.zh-CN.md) · [Compatibility](docs/compatibility.md) · [Architecture](docs/architecture.md)

![PhonePilot console in demo mode; no real device or private data](docs/images/console-light.png)

> Experimental software. Physical-device operation has been verified on one
> iPhone/iOS/Xcode combination. See the [tested scope](docs/compatibility.md)
> before relying on another configuration. The preview above uses a synthetic demo.
> Start with USB: wireless tunnel establishment failed in the recorded physical test.

## Features

- Live HEVC preview with an explicit periodic-screenshot fallback.
- An iPhone-style frame that follows the received image's aspect ratio, orientation,
  and available space. No extra notch or Dynamic Island is drawn over the image.
- Tap, swipe, long press, text entry, app launching, and hardware buttons.
- Pause/resume controls; pausing cancels current input while keeping the preview available.
- Save PNG screenshots and record the console canvas without audio.
- Ten stdio MCP tools, plus a skill for opening and using the console in a compatible AI app.
- Light/dark appearance and layouts for wide windows and narrow side panels.

PhonePilot uses [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) CoreDevice
services, rather than Apple's iPhone Mirroring window. The console does not bundle
an AI model. AI processing and usage limits belong to your chosen AI application.

## Requirements

- macOS and Python 3.11 or newer; the recorded physical-device test used Apple silicon.
- [uv](https://docs.astral.sh/uv/getting-started/installation/).
- Full Xcode with developer-device support compatible with your iPhone's iOS version.
- A USB-connected, unlocked iPhone that already trusts this Mac and has Developer Mode enabled.

Initial trust, pairing, Developer Mode, and device-support preparation are manual steps.
PhonePilot does not change them automatically. Node.js is needed only for frontend
development checks, not for running the console.

## Quick start

Download or clone this repository and open a terminal in its directory:

```sh
uv sync --locked --python 3.14
uv run --locked phonepilot
```

Open **http://127.0.0.1:8765/** in a browser, or in your AI app's browser side panel.
Connect and unlock the phone, select the detected device in **设备设置 / Device settings**,
and choose **连接 iPhone / Connect iPhone**. Wait for a current picture before controlling it.
The console interface is currently in Simplified Chinese.

On macOS, you can also run `首次安装.command`, followed by `启动 PhonePilot.command`.
The launcher reuses or starts a background hub and opens the console. Closing the launcher
does not stop the hub. The terminal quick-start command above stays in the foreground
and can be stopped with Control-C.

### Try the UI without a phone

```sh
uv run --locked phonepilot --demo --port 8766 --state-dir var-demo
```

Open **http://127.0.0.1:8766/** and connect the demo device. Demo mode never discovers
or controls real hardware and is labelled throughout the UI. Keep it separate from
your physical-device session.

## Use it from an AI app

### Agent skill

For a compatible app that discovers user skills under `~/.agents/skills`:

```sh
python3 tools/install_skill.py
```

The installer links the skill to this checkout, preserves an existing different skill,
and does not modify global MCP settings. Keep the checkout in place; moving it requires
updating the link. Skill discovery and browser-control availability depend on the app.

Example request:

> 用 PhonePilot 打开手机侧栏，先告诉我当前打开的是什么 App。

### MCP

Start the console, connect your phone, then open **智能体 / Agent → 智能体接入设置** and
copy the generated MCP configuration into your AI app's settings. The configuration
uses your own checkout and environment paths; it contains no session token. Reload
the MCP connection as required by your app.

Tools: `phone_status`, `phone_screenshot`, `phone_tap`, `phone_swipe`, `phone_long_press`,
`phone_type_text`, `phone_press_key`, `phone_press_button`, `phone_list_apps`, `phone_open_app`.

All tools reuse the console's connection. Screenshot-based pointer tools require a
fresh `observation_id`; it expires after 60 seconds and is invalidated by control
actions. Re-observe the screen after an action to verify the outcome.

## Privacy and control

The server listens on `127.0.0.1`, checks local Host/Origin values, and requires a
random session token for device APIs. Session files live in `var/` with restrictive
permissions and must not be shared. Non-ASCII text entry uses the phone clipboard
and replaces its current contents; PhonePilot does not read the old clipboard.

Screenshots, input text, and app lists are not written to disk by ordinary API calls.
Explicit screenshot/recording downloads do create files. The in-memory activity log
contains operation types and times, not input text. Screenshots supplied to an AI app
are processed under that app's rules; local device control does not imply local AI inference.
See [SECURITY.md](SECURITY.md).

## Known limits

No audio playback/recording, multi-touch, automatic unlock, automatic pairing, or
automatic Developer Mode setup. Physical-device recording, non-ASCII input,
wireless operation, multi-device operation, and long-duration stability remain
unverified. Camera/microphone use and developer-service faults can interrupt a session.
Adapting the preview to a new screen shape is not a claim of protocol compatibility
with that device.

## Development

```sh
uv sync --locked --extra dev --python 3.14
uv run --locked --extra dev pytest -q
npm ci
npm test
npm run format:check
uv build
uv run --locked --extra dev python tools/package_release.py --output dist/phonepilot-local-source.zip
```

The Python integration tests use simulated devices; frontend tests exercise frame
fitting and pointer mapping. Neither test suite connects to a physical iPhone.
GitHub Actions is configured to run checks on macOS. A local pass does not mean
the hosted workflow has run. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License and credits

[GPL-3.0-or-later](LICENSE). Transport and HID functionality builds on
pymobiledevice3 at the pinned revision recorded in `pyproject.toml` and `uv.lock`.
Keep that pin when reproducing the verified implementation: this adapter uses
upstream internal APIs. See [THIRD_PARTY.md](THIRD_PARTY.md) for attribution.
This project is not affiliated with Apple or OpenAI.

## Recovery and parcel checks

The launcher reuses a running hub or starts it in the background and opens the console. Closing the launcher does not stop the service. Setup uses the locked environment and checks the selected full Xcode. After explicit connection, the hub probes device availability, waits for the same device on interruption and bounds reconnect attempts. Pause is preserved; uncertain input is never replayed. Video failures use screenshots while retrying live video. A hub restart requires device selection again.

The bundled skill includes a read-only Taobao parcel-check workflow: inspect each order and split shipment, report observed logistics events and incomplete coverage. An AI client still executes the workflow; copying its prompt does not start a built-in agent. Personal logistics data is excluded from source packages.

### USB and Wi-Fi

Choose either transport in Device settings. For first-time wireless setup, connect an unlocked, trusted iPhone over USB, select it and explicitly choose **为这部手机准备无线连接 / Prepare wireless**. This saves a private pairing record locally. Then choose Wi-Fi, keep the phone and Mac on the same network and refresh. Existing network-paired devices can also be discovered. Normal connection and recovery never pair a new device. Wi-Fi mode cannot silently fall back to USB, and recovery retains the selected transport. Wireless code has simulated regression coverage. In the recorded physical test, existing-pair identity validation passed but wireless tunnel establishment failed; cable-free control has not passed acceptance.

If discovery is unavailable, enter the phone’s private IPv4 address in Wi-Fi settings. The existing pairing and returned device identity must match the selected target. A saved pairing does not establish availability; update the address if DHCP changes it.
