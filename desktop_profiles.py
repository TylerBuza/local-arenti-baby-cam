"""Read camera stream choices and obtain a local URL; no encoder/settings writes."""
from urllib.parse import urlsplit, urlunsplit
from xml.sax.saxutils import escape

from onvif_local import DEVICE, MEDIA, SCHEMA, SOAP, OnvifClient, local_url


def parse_profiles(root):
    choices = []
    for profile in root.findall(f'.//{{{MEDIA}}}Profiles')[:16]:
        token = profile.get('token')
        codec = profile.findtext(f'.//{{{SCHEMA}}}VideoEncoderConfiguration/{{{SCHEMA}}}Encoding')
        try:
            width = int(profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Width', '0'))
            height = int(profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Height', '0'))
        except ValueError:
            continue
        if (isinstance(token, str) and 0 < len(token) <= 256 and codec in ('H264', 'H265')
                and 1 <= width <= 4096 and 1 <= height <= 2160):
            choices.append({'token': token, 'width': width, 'height': height, 'codec': codec})
    choices.sort(key=lambda p: p['width'] * p['height'], reverse=True)
    if not choices:
        raise ValueError('No usable local stream profiles')
    return choices


def stream_choice(credentials, quality):
    if quality not in ('high', 'low'):
        raise ValueError('Unknown stream quality')
    client = OnvifClient(credentials['username'], credentials['password'])
    client.request_timeout = (1.5, 2)
    def call(url, namespace, action, inner=''):
        code, root = client.soap(url, namespace, action, inner)
        if code != 200 or root is None or root.find(f'.//{{{SOAP}}}Fault') is not None:
            raise RuntimeError('Local stream profile read failed')
        return root
    try:
        client.synchronize_clock(credentials['deviceUrl'])
        caps = call(credentials['deviceUrl'], DEVICE, 'GetCapabilities', '<t:Category>All</t:Category>')
        media = caps.findtext(f'.//{{{SCHEMA}}}Media/{{{SCHEMA}}}XAddr')
        local_url(media)
        choices = parse_profiles(call(media, MEDIA, 'GetProfiles'))
        selected = choices[0] if quality == 'high' else choices[-1]
        inner = ('<t:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport>'
                 '<tt:Protocol>RTSP</tt:Protocol></tt:Transport></t:StreamSetup>'
                 f'<t:ProfileToken>{escape(selected["token"])}</t:ProfileToken>')
        root = call(media, MEDIA, 'GetStreamUri', inner)
        uri = root.findtext(f'.//{{{SCHEMA}}}Uri')
        parsed = urlsplit(uri or '')
        if parsed.username or parsed.password:
            uri = urlunsplit((parsed.scheme, f'{parsed.hostname}:{parsed.port or 554}', parsed.path, parsed.query, ''))
        local_url(uri, schemes=('rtsp',))
        # Profile names/tokens/URLs stay in the worker; UI receives only dimensions.
        public = [{'width': p['width'], 'height': p['height'], 'codec': p['codec']} for p in choices]
        return uri, public
    finally:
        client.close()
