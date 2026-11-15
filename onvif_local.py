"""Narrow local-only ONVIF/RTSP reader. Never records, plays, or logs media."""
import base64
from datetime import datetime, timezone
import hashlib
import secrets
import socket
import time
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

from lan_tools import camera_host

SOAP = 'http://www.w3.org/2003/05/soap-envelope'
DEVICE = 'http://www.onvif.org/ver10/device/wsdl'
MEDIA = 'http://www.onvif.org/ver10/media/wsdl'
SCHEMA = 'http://www.onvif.org/ver10/schema'
WSSE = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd'
WSU = 'http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd'


def local_url(url, schemes=('http', 'https')):
    parsed = urlsplit(url)
    if (parsed.scheme not in schemes or parsed.hostname != camera_host() or parsed.username
            or parsed.password or parsed.fragment or len(url) > 4096
            or any(ord(c) < 33 for c in url) or not 0 < (parsed.port or 80) < 65536):
        raise ValueError('Not an approved camera-local URL')
    return parsed


class OnvifClient:
    allowed_actions = frozenset(('GetCapabilities', 'GetProfiles', 'GetStreamUri', 'GetSystemDateAndTime', 'GetDeviceInformation'))
    def __init__(self, username, password):
        self.username, self.password = username, password
        self.session = requests.Session()
        self.session.trust_env = False
        self.clock_offset = 0
        self.request_timeout = (3, 6)

    def close(self):
        self.session.close()

    def soap(self, url, namespace, action, inner='', authenticated=True):
        local_url(url)
        if action not in self.allowed_actions:
            raise ValueError('Only read-only ONVIF actions are permitted')
        header = ''
        if authenticated:
            created = datetime.fromtimestamp(time.time() + self.clock_offset, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
            nonce = secrets.token_bytes(16)
            digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
            header = (f'<wsse:Security s:mustUnderstand="1"><wsse:UsernameToken>'
                      f'<wsse:Username>{escape(self.username)}</wsse:Username>'
                      f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
                      f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
                      f'<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security>')
        body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="{SOAP}" xmlns:wsse="{WSSE}" '
                f'xmlns:wsu="{WSU}" xmlns:t="{namespace}" xmlns:tt="{SCHEMA}">'
                f'<s:Header>{header}</s:Header><s:Body><t:{action}>{inner}</t:{action}></s:Body></s:Envelope>').encode()
        auth = HTTPDigestAuth(self.username, self.password) if authenticated else None
        with self.session.post(url, data=body, headers={
            'Content-Type': f'application/soap+xml; charset=utf-8; action="{namespace}/{action}"'},
            auth=auth, allow_redirects=False, timeout=self.request_timeout, stream=True) as response:
            code = response.status_code
            raw = response.raw.read(256 * 1024 + 1)
            if len(raw) > 256 * 1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('ONVIF response too large')
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            root = None
        return code, root

    def synchronize_clock(self, url):
        code, root = self.soap(url, DEVICE, 'GetSystemDateAndTime', authenticated=False)
        if code != 200 or root is None:
            return
        utc = root.find(f'.//{{{SCHEMA}}}UTCDateTime')
        if utc is None:
            return
        def integer(part):
            return int(utc.findtext(f'.//{{{SCHEMA}}}{part}'))
        stamp = datetime(integer('Year'), integer('Month'), integer('Day'),
                         integer('Hour'), integer('Minute'), integer('Second'), tzinfo=timezone.utc)
        self.clock_offset = stamp.timestamp() - time.time()

    def stream_uri(self, device_url):
        self.synchronize_clock(device_url)
        code, root = self.soap(device_url, DEVICE, 'GetCapabilities', '<t:Category>All</t:Category>')
        if code != 200 or root is None or root.find(f'.//{{{SOAP}}}Fault') is not None:
            raise RuntimeError('Authenticated ONVIF capabilities unavailable')
        media = root.find(f'.//{{{SCHEMA}}}Media/{{{SCHEMA}}}XAddr')
        media_url = media.text if media is not None else device_url
        local_url(media_url)
        code, root = self.soap(media_url, MEDIA, 'GetProfiles')
        if code != 200 or root is None or root.find(f'.//{{{SOAP}}}Fault') is not None:
            raise RuntimeError('Authenticated ONVIF profiles unavailable')
        profiles = root.findall(f'.//{{{MEDIA}}}Profiles')
        if not profiles:
            raise RuntimeError('No ONVIF media profile')
        def score(profile):
            codec = profile.findtext(f'.//{{{SCHEMA}}}VideoEncoderConfiguration/{{{SCHEMA}}}Encoding', '')
            width = profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Width', '0')
            height = profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Height', '0')
            return (codec in ('H264', 'H265'), int(width) * int(height))
        selected = max(profiles, key=score)
        token = selected.get('token')
        if not token:
            raise RuntimeError('No profile token')
        inner = (f'<t:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>'
                 f'<tt:Protocol>RTSP</tt:Protocol></tt:Transport></t:StreamSetup>'
                 f'<t:ProfileToken>{escape(token)}</t:ProfileToken>')
        code, root = self.soap(media_url, MEDIA, 'GetStreamUri', inner)
        if code != 200 or root is None:
            raise RuntimeError('RTSP URI unavailable')
        uri = root.findtext(f'.//{{{SCHEMA}}}Uri')
        if not uri:
            raise RuntimeError('No RTSP URI')
        parsed = urlsplit(uri)
        if parsed.username or parsed.password:
            uri = urlunsplit((parsed.scheme, f'{parsed.hostname}:{parsed.port or 554}', parsed.path, parsed.query, ''))
        local_url(uri, schemes=('rtsp',))
        return uri, len(profiles)


def anonymous_rtsp_status(uri):
    parsed = local_url(uri, schemes=('rtsp',))
    with socket.create_connection((parsed.hostname, parsed.port or 554), timeout=3) as sock:
        sock.settimeout(3)
        sock.sendall((f'DESCRIBE {uri} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n'
                      'User-Agent: Arenti-Local-Auth-Test\r\n\r\n').encode())
        data = b''
        while b'\r\n\r\n' not in data and len(data) < 16384:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    first = data.split(b'\r\n', 1)[0].split()
    return int(first[1]) if len(first) >= 2 and first[0] == b'RTSP/1.0' else None


def authenticated_uri(uri, username, password):
    parsed = local_url(uri, schemes=('rtsp',))
    return urlunsplit((parsed.scheme, f'{quote(username, safe="")}:{quote(password, safe="")}@{parsed.hostname}:{parsed.port or 554}',
                       parsed.path, parsed.query, ''))


def validate_stream(uri, username, password, seconds=5):
    import av
    av.logging.set_level(av.logging.PANIC)
    summary = {'cloudRequests': 0, 'mediaSaved': False, 'videoFrames': 0, 'audioFrames': 0}
    started = time.monotonic()
    with av.open(authenticated_uri(uri, username, password), timeout=(5, 5),
                 options={'rtsp_transport': 'tcp', 'rw_timeout': '5000000'}) as source:
        summary['videoTracks'] = len(source.streams.video)
        summary['audioTracks'] = len(source.streams.audio)
        for packet in source.demux():
            if time.monotonic() - started > 15:
                raise TimeoutError('Camera video validation timed out')
            for frame in packet.decode():
                if packet.stream.type == 'video':
                    summary['videoFrames'] += 1
                    summary['width'], summary['height'] = frame.width, frame.height
                elif packet.stream.type == 'audio':
                    summary['audioFrames'] += 1
                    summary['audioSampleRate'] = frame.sample_rate
            if time.monotonic() - started >= seconds and summary['videoFrames'] >= 15:
                break
    summary['elapsedSeconds'] = round(time.monotonic() - started, 2)
    return summary
