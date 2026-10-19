# Contributing

Use macOS for the Python test suite. Python 3.14 is the recorded development
environment; Python 3.11+ is the declared package range. Node.js 24 is used by CI
for frontend checks. No physical phone is required for automated tests.

```sh
uv sync --locked --extra dev --python 3.14
npm ci
uv run --locked --extra dev pytest -q
npm test
npm run format:check
uv build
```

Use `npm run format` when changing frontend files. Keep the pinned pymobiledevice3
commit unless intentionally updating the native adapter and re-testing its internal
API assumptions. Regenerate `uv.lock` when Python dependencies change.

## Useful checks

- Preserve the separation between connected, current picture, unlocked, and controllable.
- Test both portrait and landscape frame geometry, narrow panels, and pointer mapping.
- Keep demo mode explicit and incapable of reaching physical-device discovery.
- Keep local authentication, Origin/Host checks, and private state-file permissions.
- Do not log input content, device identifiers, screenshots, or credentials.
- Include the expected behavior and validation performed in pull requests. State
  whether device testing was simulated or physical.

## Source archives

```sh
uv run --locked --extra dev python tools/package_release.py --output dist/phonepilot-local-source.zip
```

The packager uses an explicit source allowlist, rejects symlinks and accidental
credentials, and excludes runtime state, environments, recordings, and personal captures.
Only generated demo previews under `docs/images/` are intended for public documentation.
Do not upload the directory of a running personal console wholesale.

## Issues

Use the bug-report template and include macOS, CPU architecture, Python, iOS,
Xcode, and whether the problem occurs in demo mode. Redact names, identifiers,
addresses, messages, tokens, and screenshots of personal content. Follow
[SECURITY.md](SECURITY.md) for potential vulnerabilities.
