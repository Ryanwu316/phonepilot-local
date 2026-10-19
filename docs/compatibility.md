# Compatibility and verification

Recorded physical-device verification: **2026-10-17–18**, macOS on Apple silicon,
Python 3.14.7, Xcode 27.1, and one iPhone running iOS 27.0.1. This is a test record,
not a claim of complete support for every device or combination in the package's
declared Python range.

| Capability | Recorded evidence |
| --- | --- |
| Physical connection | USB, with trust and Developer Mode prepared manually |
| Live preview | HEVC frame updates observed in an AI app's browser side panel |
| App list and launch | Physical-device app enumeration and app opening observed |
| Pointer input | Taps and swipes observed; Calculator result independently re-observed |
| Screenshot | Physical-device PNG saved and inspected |
| Pause/resume | Control disabled while paused; preview remained available |
| Recording | Verified with demo frames only; physical-device recording unverified |
| Non-ASCII input | Implemented clipboard-paste path; physical-device verification pending |
| Wireless | Preparation passed; discovery intermittent; session establishment failed; cable-free acceptance pending |
| Multiple devices | Unverified |
| Long-duration stability | Unverified; a stream interruption and recovery were observed |
| New or unusual screen shapes | Synthetic portrait, landscape, 4:3, and square fitting tests; not physical-model validation |

The native control path does not depend on the macOS iPhone Mirroring app. It does
depend on compatible CoreDevice/developer services. Xcode being installed alone
does not establish device readiness. Unsupported or locked devices can be visible
without providing a usable screen or control session.

New iPhone models, future iOS releases, foldable/multi-screen devices, and changed
upstream protocols require separate validation. Automatic preview fitting is a UI
capability, not a device-support guarantee.

## Automated checks

The Python suite covers local access boundaries, input validation, observation
expiry, cancellation, lifecycle, stdio MCP integration, skill reuse/installation,
and source-release packaging. The frontend suite covers geometry, resize/rotation,
pointer mapping, and stale-picture recovery. These tests use simulated devices.

Run the commands in [CONTRIBUTING.md](../CONTRIBUTING.md) for current results.
GitHub Actions configuration is included; its hosted status is only established
after it has actually run in a repository.

## Recovery update: 2026-10-18

A clean source archive was extracted into a separate directory and installed into
a fresh Python 3.14.7 environment using the locked dependencies. Host checks
identified the selected full Xcode 27.1 and its build version.

The update has 65 Python tests and 16 frontend tests. Fault injection covers
connection loss without a browser, same-device return, bounded reconnect attempts,
disconnect during reconnect, pause preservation, old-observation rejection,
uncertain-input non-replay, connection-epoch picture invalidation, and browser
authorization renewal after restart. These are simulated faults, not proof of
physical USB unplug/replug or long-duration stability.

The Taobao parcel skill now defines order and split-shipment coverage, observed
logistics timestamps, incomplete-result reporting, and resume checkpoints. Physical
acceptance of this updated flow remains incomplete: the physical USB session
opened the pending-receipt list and one logistics detail, but full order and
split-package coverage has not been completed. The workflow is an AI instruction,
not an autonomous parcel crawler built into the console.

Run only one CoreDevice mirror on the same iPhone at a time. Device-side media
cleanup follows the pinned upstream implementation and is not designed to
coordinate with another program controlling that same phone.

Wireless selection and a separate network transport have been implemented, including authenticated paired-device discovery and explicit USB-assisted initial pairing. The transport selector and resource cleanup are covered with simulated tests. A USB cable being present during a Wi-Fi test does not prove cable-free operation. Wireless physical acceptance remains pending until the cable is removed and the same phone still provides a fresh screen and working control.
