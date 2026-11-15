# Privacy by reducing what has to leave home

A camera in a child's room deserves a small, understandable exposure surface.
Cloud accounts, remote viewing endpoints and provider-side access add places where
credentials or video access can go wrong. Removing those dependencies from normal
viewing reduces exposure; it is not proof that a device is invulnerable.

This concern is grounded in documented incidents. The FTC's 2023 Ring case alleged
excessive employee/contractor access and inadequate account protections; in 2024
the FTC sent refunds following that settlement.
[Primary source: FTC, April23,2024](https://www.ftc.gov/news-events/news/press-releases/2024/04/ftc-sends-refunds-ring-customers-stemming-2023-settlement-over-charges-company-failed-block).
That is a general cloud-camera example, **not a vulnerability claim about Arenti**.
This project has not established that a particular Arenti firmware is exploitable.

## What stays local

- Camera video/control: local RTSP/ONVIF. Credentials stay on the Windows PC,
  encrypted for the Windows user using DPAPI. Discovery sends bounded local
  multicast/broadcast probes only when the user requests it.
- Phone access: your Windows connector, pinned CA and individual client certificates.
  QR enrollment happens on the LAN and expires after five minutes. Revocation is
  available in Phone access. The public APK contains no pre-enrolled home access.
- Recording: only when the Windows user explicitly saves a screenshot or recording.
  Android/connector streaming is in-memory; no analytics or advertising SDK.
- App installers/builds download dependencies. Optional remote access uses external
  networking services. Those are separate from local camera viewing.

DPAPI does not protect against malware running as the same Windows user. Plain
camera-side RTSP/ONVIF is not end-to-end encrypted by the phone's TLS connection.
Use a trusted home LAN or a camera VLAN with only the necessary viewer access.

## Block the camera after setup

1. **Make local viewing work first.** Confirm ONVIF is enabled and password-protected,
   local live video/audio works, and reconnects work. Record your router configuration
   and a targeted undo before applying camera-specific rules.
2. **Identify and reserve the camera.** Use its hardware identity and DHCP reservation,
   not an IP that might later belong to another device. On a camera VLAN, allow the
   Windows viewer to reach its local services and keep unwanted lateral access blocked.
3. **Deny camera-to-WAN traffic across all protocols, for IPv4 and IPv6.** Preserve
   required LAN access. Remove camera port forwards and prevent UPnP from creating
   new ones. An inbound block alone does not stop outbound cloud sessions.
4. **Account for bypass paths.** Check IPv6 routing, existing stateful sessions,
   alternate uplinks and the router's DNS proxy. A camera may still send data in DNS
   queries to a forwarding resolver; use a local-only DNS policy for that device or
   block its DNS access if the tested local setup does not need it. Supply local time
   services if required rather than assuming all NTP must be on the internet.
5. **Verify and retest.** Inspect per-device firewall counters/logs and captures on
   the appropriate interfaces. Confirm new external connections fail on both IP
   families while local viewing still works. Retest after DHCP renewal/reconnect and,
   when it is safe to interrupt monitoring, a camera restart. A saved rule, vendor
   “offline” badge or stale NAT entry alone is not conclusive.
6. **Plan maintenance.** Vendor notifications, remote vendor viewing, firmware updates,
   cloud recordings, clocks or sensors may stop working. Review what you need and
   deliberately arrange updates. Do not broadly disable your router's security or
   reset all household connections to test a single camera.

This repository supplies guidance, not automatic router modification or proof of
your network's isolation. Router controls differ. Keep a working monitoring option
available while making changes.

## Remote viewing

Leave remote configuration empty for LAN-only use. If you later configure a private
VPN or an authenticated connector route, keep the camera itself isolated. Funnel
is a public endpoint with client-certificate authorization, not a private LAN.
Do not forward camera RTSP/ONVIF directly to the internet. The Windows connector
must remain awake; it is a security and availability boundary of its own.

## Website

The GitHub Pages site contains no project analytics, embedded video, remote fonts
or third-party scripts. GitHub still operates the hosting and may process request
metadata under its own privacy policy. Clicking release or reference links contacts
those destinations. Do not post passwords, pairing QR codes or private recordings
in public issues.
