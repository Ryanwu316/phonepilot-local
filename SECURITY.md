# Security

PhonePilot controls the iPhone that you explicitly connect. Run it only on a trusted
Mac and leave its listener on loopback. The project currently has one experimental
release line, 0.1.x; no long-term support or security-response SLA is promised.

## Boundaries

- The device API requires a random local session token. Browser cookies are
  HttpOnly and SameSite=Strict. Host and Origin checks reject non-local use.
- `var/runtime.json` contains a credential. The state directory is created with
  mode 0700 and that file with mode 0600. Do not commit or share it.
- An MCP client with access to your state directory can use the connected session.
  Treat local agent and plugin access accordingly.
- The console does not automatically unlock, trust, pair, or enable Developer Mode.
- The activity log excludes input text. Explicitly downloaded screenshots and
  recordings can contain private information. AI screenshot processing happens in
  your selected AI app, under that application's terms and settings.
- Non-ASCII text input replaces the phone clipboard through an explicit paste action.

## Reporting

If the repository has GitHub private vulnerability reporting enabled, use its
Security tab. Otherwise open an issue requesting a private reporting channel,
without describing exploitation details or sharing credentials. Do not attach
runtime files, pairing material, device identifiers, or screenshots of private data.

Share a minimal reproduction using demo mode when possible. Public reports should
contain only sanitized environment information and the affected version.
