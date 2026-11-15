"""Bounded, user-initiated LAN discovery. No vendor account or internet probes."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import ipaddress
import json
import select
import socket
import time
import uuid
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

_setup_host = ContextVar('setup_camera_host', default=None)
_ranges = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))


def private_ipv4(value):
    ip = ipaddress.ip_address(value)
    if ip.version != 4 or not any(ip in network for network in _ranges):
        raise ValueError('Use a private LAN IPv4 address')
    return str(ip)


def camera_host():
    if _setup_host.get():
        return _setup_host.get()
    import protected_store
    value = protected_store.load()
    if value.get('state') != 'verified':
        raise ValueError('Set up your camera first')
    return private_ipv4(urlsplit(value['deviceUrl']).hostname)


@contextmanager
def setup_host(host):
    token = _setup_host.set(private_ipv4(host))
    try:
        yield
    finally:
        _setup_host.reset(token)


def interfaces():
    import psutil
    found = []
    for name, addresses in psutil.net_if_addrs().items():
        if not psutil.net_if_stats().get(name) or not psutil.net_if_stats()[name].isup:
            continue
        for item in addresses:
            if item.family != socket.AF_INET:
                continue
            try:
                host = private_ipv4(item.address)
                network = ipaddress.ip_network(host + '/' + item.netmask, strict=False)
                found.append((host, network))
            except (ValueError, TypeError):
                continue
    return found[:8]


def connector_origin():
    available = interfaces()
    if not available:
        return ''
    try:
        target = ipaddress.ip_address(camera_host())
        available.sort(key=lambda pair: target not in pair[1])
    except Exception:
        pass
    return f'https://{available[0][0]}:7443'


def discovery_urls(data, sender):
    """Never trust a response URL that points elsewhere (including external hosts)."""
    private_ipv4(sender)
    if len(data) > 8192 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        return []
    try:
        root = ET.fromstring(data)
        urls = [url for element in root.iter() if element.tag.endswith('}XAddrs') for url in (element.text or '').split()]
    except ET.ParseError:
        try:
            value = json.loads(data.rstrip(b'\x00'))
            if not isinstance(value, dict) or not any(value.get(k) for k in ('sn', 'licenseId', 'p2p_uuid')):
                return []
            urls = [f'http://{sender}:8000/onvif/device_service']
        except (ValueError, UnicodeDecodeError):
            return []
    result = []
    for url in urls[:16]:
        try:
            parsed = urlsplit(url)
            if (parsed.scheme in ('http', 'https') and parsed.hostname == sender
                    and not parsed.username and not parsed.password and not parsed.fragment
                    and 0 < (parsed.port or 80) < 65536 and len(url) < 512
                    and not any(ord(c) < 33 for c in url)):
                result.append(url)
        except ValueError:
            pass
    return result


def discover(seconds=4):
    sockets, results = {}, set()
    probe = (f'<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
        f'xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
        f'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
        f'xmlns:dn="http://www.onvif.org/ver10/network/wsdl"><s:Header>'
        f'<a:MessageID>uuid:{uuid.uuid4()}</a:MessageID>'
        f'<a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>'
        f'<a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>'
        f'</s:Header><s:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>'
        f'</d:Probe></s:Body></s:Envelope>').encode()
    try:
        for host, network in interfaces():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind((host, 0))
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host))
                sock.sendto(probe, ('239.255.255.250', 3702))
                sock.sendto(b'{"discovery":{}}', (str(network.broadcast_address), 3703))
                sockets[sock] = network
            except OSError:
                sock.close()
        deadline, replies = time.monotonic() + min(6, max(0.1, seconds)), 0
        while sockets and time.monotonic() < deadline and replies < 128:
            ready, _, _ = select.select(list(sockets), [], [], min(.2, max(0, deadline-time.monotonic())))
            for sock in ready:
                data, address = sock.recvfrom(8193)
                replies += 1
                if ipaddress.ip_address(address[0]) in sockets[sock]:
                    results.update(discovery_urls(data, address[0]))
        return sorted(results)[:32]
    finally:
        for sock in sockets:
            sock.close()


def device_identity(client, url):
    from onvif_local import DEVICE
    client.synchronize_clock(url)
    code, root = client.soap(url, DEVICE, 'GetDeviceInformation')
    if code != 200 or root is None:
        raise ValueError('Authenticated camera identity unavailable')
    fields = {element.tag.rsplit('}', 1)[-1]: (element.text or '') for element in root.iter()}
    serial = fields.get('SerialNumber', '')
    if not 1 <= len(serial) <= 256:
        raise ValueError('Camera did not return a stable serial number')
    return hashlib.sha256(serial.encode()).hexdigest()


def verify_local_hash(expected_hash):
    import hmac
    import protected_store
    from onvif_local import OnvifClient
    value = protected_store.load()
    client = OnvifClient(value['username'], value['password'])
    try:
        if not hmac.compare_digest(device_identity(client, value['deviceUrl']), expected_hash):
            raise ValueError('Camera identity changed; review setup')
    finally:
        client.close()
    return True
