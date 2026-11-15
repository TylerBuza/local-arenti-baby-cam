"""Explicit LAN + Tailscale binds, never an all-interface/public listener."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit

from gateway.identity import checked_url, checked_funnel_url

TAILSCALE_IPV4 = ipaddress.ip_network('100.64.0.0/10')
FUNNEL_LOCAL_PORT = 7444


def tailscale_ipv4():
    """Read this PC's existing VPN IP. Never start/login/configure Tailscale."""
    executable = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe'
    if os.name != 'nt' or not executable.is_file():
        return None
    try:
        result = subprocess.run([str(executable), 'ip', '-4'], capture_output=True,
            text=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if result.returncode != 0 or len(result.stdout) > 1024:
            return None
        lines = result.stdout.splitlines()
        if len(lines) != 1:
            return None
        address = ipaddress.ip_address(lines[0].strip())
        return str(address) if address.version == 4 and address in TAILSCALE_IPV4 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def tailscale_dns_name():
    """Read only this existing node's DNS name; never change tailnet settings."""
    executable = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Tailscale' / 'tailscale.exe'
    if os.name != 'nt' or not executable.is_file():
        return None
    try:
        result = subprocess.run([str(executable), 'status', '--json', '--peers=false'],
            capture_output=True, text=True, timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        if result.returncode != 0 or len(result.stdout) > 65536:
            return None
        status = json.loads(result.stdout)
        host = status['Self']['DNSName'].rstrip('.')
        if status.get('BackendState') != 'Running' or not host or len(host) > 253:
            return None
        checked_funnel_url('https://' + host)
        return host
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired):
        return None


def listener_addresses(local_url, remote_url='', *, funnel=False):
    local = urlsplit(checked_url(local_url, local=True))
    addresses = [(local.hostname, local.port or 443)]
    if funnel:
        checked_funnel_url(remote_url)
        if (local.hostname, local.port or 443) == ('127.0.0.1', FUNNEL_LOCAL_PORT):
            raise ValueError('The enrollment and Funnel listeners must be separate')
        return addresses + [('127.0.0.1', FUNNEL_LOCAL_PORT)]
    if remote_url:
        remote = urlsplit(checked_url(remote_url))
        try:
            address = ipaddress.ip_address(remote.hostname)
        except ValueError:
            address = None
        if address is not None and address.version == 4 and address in TAILSCALE_IPV4:
            addresses.append((str(address), remote.port or 443))
    # A kernel bind fails if the configured VPN IP is not assigned to this PC.
    # A generic external endpoint remains usable for legacy opaque-relay setups,
    # but is never bound locally and never opens a wildcard interface.
    return addresses
