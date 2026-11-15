# Local Arenti Baby Cam

**Your baby camera. Your network.** A local-first Windows viewer and paired Android
app for ONVIF/RTSP-enabled Arenti/D3 cameras. Independent community project; not
affiliated with Arenti. **v0.1.0-beta** — initial public adaptation of a home project.

[Project website](https://tylerbuza.github.io/local-arenti-baby-cam/) ·
[Downloads](https://github.com/TylerBuza/local-arenti-baby-cam/releases/tag/v0.1.0-beta) ·
[Privacy & isolation](PRIVACY.md)

## Check compatibility first

- Camera firmware must already expose **password-protected ONVIF and RTSP**.
  Wi-Fi setup may initially require the manufacturer's app. This project cannot
  enable hidden firmware features or derive local passwords from cloud credentials.
- Arenti/D3 is the initial target, not a claim that every Arenti model works.
  The underlying viewer has been used on one D3; the new generic setup is tested
  with synthetic responses. Fresh third-party hardware setup remains unverified.
- Windows10/11 x64, same LAN; Android8 or later. The Windows EXE sets up its own
  Python runtime; a separate Python installation is not required.
- Android connects through the Windows connector. The PC must stay running and
  awake. This first release is not a standalone Android-to-camera client.

## Install & connect

1. Download **Local-Arenti-Baby-Cam-Windows-Setup-0.1.0-beta.exe** from Releases.
2. Run the installation wizard. It downloads a hash-verified private Python3.13
   x64 runtime from the Python project's NuGet distribution and pinned direct
   dependencies from PyPI. Allow internet access during setup, several minutes,
   and approximately 1.6 GB of disk space. Installation is per user, with a Start
   menu entry, optional desktop shortcut and Windows uninstaller. It does not
   require administrator access or change router/firewall rules. The community
   installer is unsigned; its SHA256 is published with the release.
3. Choose **Find cameras on this network**, select the camera's ONVIF service URL,
   enter its local username/password and choose **Test local video and save**.
   Manual URL entry is available if multicast discovery is unavailable.
4. Credentials are encrypted with Windows DPAPI. Subsequent launches reconnect
   to the saved camera. Reserve the camera and connector PC's LAN IPs in your router; rerun Camera setup if
   it changes. There is no password guessing, cloud login or vendor fallback.
5. Install the public Android APK. In Windows open **Phone access → Pair Android**;
   on Android tap **Pair phone** and scan. Use the same LAN for first pairing.
   The short-lived QR grants one phone its own certificate, not the camera password.
   After pairing the phone reconnects automatically. The APK contains no home address
   or shared family-access key. Allow inbound Private-network access to Windows
   TCP7443 if your local firewall blocks phone pairing.
6. After testing local video/audio and reconnects, follow [the camera-only WAN
   isolation guide](PRIVACY.md#block-the-camera-after-setup). Verify the block rather
   than assuming an “offline” label proves it.

## Features

Windows: live video/audio, Auto/High/Low stream selection, recovery, muted startup,
fullscreen/pop-out, bounded pan/tilt, explicit local screenshots/MP4 recording,
tray operation and optional idle-preview Startup. Opening the viewer autoplays;
manual Stop suppresses autoplay until Start or a new process.

Android: paired TLS, video/audio, Auto quality, recovery, zoom, fullscreen and native
PiP. Only the camera's embedded timestamp appears. No health overlay, ads or analytics.
No phone microphone capture. Android and the connector do not save video.

## Local first, accurately

Normal camera viewing uses local ONVIF/RTSP, not the vendor account/relay. **This does
not itself prevent the camera firmware from contacting the internet.** Enforce
that at the router. Camera-to-PC RTSP is not encrypted: trust/segment your LAN.
Phone-to-PC uses TLS and per-phone client certificates.

Remote access is optional and off by default. The connector retains optional
Tailscale-address/Funnel support; setting up those services is separate and adds
internet/service dependencies. Do not expose the camera's own ONVIF/RTSP ports.
Initial install/build requires internet for Python/PyPI/Gradle dependencies.

## Development

Windows: `py -3.13 -m venv .venv`, install `requirements.txt`, then run
`.venv\Scripts\python.exe desktop_app.py`. First run opens setup. Source configuration
uses `%LOCALAPPDATA%\LocalArentiBabyCam` too; test only with isolated fixtures.
Run offline checks with `python -m unittest discover -s tests`.

Android: JDK17+, Android SDK36; `android\gradlew.bat -p android testDebugUnitTest
assembleRelease lintRelease`. The release APK is unsigned until you apply your own
signing key with Android's `apksigner`. Signing keys never belong in this repository.
The public package ID is `dev.tvmodding.localarentibabycam`.

### Windows installer build

Run `Build-Installer.ps1 -Compiler "PATH\TO\ISCC.exe"` with Inno Setup6.7+ and
the development `.venv` containing `requirements.txt`. The script stages an
explicit source allowlist, draws the app icon and builds the EXE in `dist`.
It does not include private state, runtime packages or camera recordings.
`installer/Bootstrap-Runtime.ps1` downloads and verifies the private Python
archive; dependencies download from PyPI during installation. Upstream licenses
remain in the installed runtime. See [third-party notices](THIRD-PARTY-NOTICES.md).

The original **source setup ZIP** remains available as an alternative and still
requires Python3.13 x64 plus its `py` launcher; extract and run `Setup.cmd`.
It does not provide the EXE's automatic Python bootstrap or uninstaller.

## Scope & limitations

No firmware flashing, vendor-service bypass, local talkback or independent fresh
temperature API. D3 movement uses zero-velocity ContinuousMove for Stop; other
models' pan/tilt behavior is not certified. No medical alerts, guaranteed uptime
or replacement for an appropriate dedicated monitor. Keep software/firmware
updated using a deliberate maintenance process if the camera normally stays isolated.

To update: exit the viewer from the tray, then run the installer. To uninstall an
EXE installation: exit the viewer and use Windows Settings → Apps → Local Arenti
Baby Cam → Uninstall. Its runtime and owned shortcuts are removed. For a source
ZIP installation, disable Start with Windows and remove the install directory/shortcuts.
Camera credentials and phone enrollment remain in `%LOCALAPPDATA%\LocalArentiBabyCam`
until you explicitly delete that data.
