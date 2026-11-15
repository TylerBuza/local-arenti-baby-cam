# v0.1.0-beta

First public, local-first Windows/Android release for password-protected,
ONVIF/RTSP-enabled Arenti/D3 cameras.

## Downloads

- **Windows installer EXE (added after the initial release):** a per-user setup
  wizard with automatic private Python runtime download, dependency installation,
  Start menu/optional desktop shortcuts and an uninstaller. No manual Python
  installation is required. Internet is needed during setup; allow about 1.6 GB
  of disk space. The community installer is unsigned.
- **Alternative Windows source setup ZIP:** extract, install Python 3.13 x64 from python.org, then run
  `Setup.cmd`. The per-user installer downloads dependencies from PyPI. This is
  source-based setup, not a standalone signed executable.
- **Android APK:** Android 8 or newer. Pair with the Windows connector using its
  short-lived QR. The PC must remain awake. No preconfigured camera or family key.
- **SHA256SUMS.txt:** hashes of the Windows EXE, original Windows ZIP and Android APK.

After one-time local setup and phone pairing, the apps reconnect automatically.
Reserve the camera and connector PC's LAN addresses for reliable reconnects.

## Validation

- Added EXE: clean isolated install with its downloaded Python3.13.17 runtime,
  installed UI and synthetic gateway checks, in-place installer rerun, Start menu
  shortcut target and uninstall passed. Uninstall preserved an unowned test file.
  Preflight rejected an unrelated folder and a simulated active viewer without
  terminating it. All 37 installed payload files matched the build manifest.
- 7 offline Python checks: discovery bounds, local-origin confinement, authenticated
  setup and refusal to save when anonymous access or video checks fail.
- Clean isolated Windows installer run, installed disconnected UI self-test and
  synthetic gateway test, including DPAPI, loopback TLS and four shared-input viewers.
- 72 Android JVM tests, release build and release lint passed.
- API 36 emulator: fresh unpaired public installation, QR-payload enrollment,
  Android Keystore identity, automatic synthetic playback, hidden clock/health
  overlays and manual Stop passed.
- Source and download contents reviewed for personal endpoints, private keys,
  enrollment payloads and unintended runtime/build files.
- Project site checked at desktop and mobile widths.
- Redesigned site checked at 320, 390, 768 and 1440 pixels, including internal
  links, local images, FAQ interactions and absence of horizontal overflow.

These checks use synthetic media and responses. The underlying viewer has run on
one D3, but this generic onboarding flow has not yet been independently validated
on another household's hardware. Public-package installation on a physical phone
is not part of this release's acceptance evidence.

## Compatibility and privacy

The project cannot enable hidden ONVIF features or recover local passwords from a
cloud account. Android requires the Windows connector. Remote access defaults off.
Camera-to-PC RTSP/ONVIF is not encrypted; protect the LAN. The app's local viewing
does not block firmware cloud traffic: follow `PRIVACY.md` to apply and verify
camera-only IPv4/IPv6 WAN restrictions at your router after local setup works.

Separate public package ID: `dev.tvmodding.localarentibabycam`.
Android signing certificate SHA-256:
`eae74242052f905551688f54214a20b48e2f8ef4a9fd0d74fdbe7037e0fd9d05`.
