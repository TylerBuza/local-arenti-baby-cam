"""Read-only dashboard widget adapter. No Owlet login, collector, alerts or storage."""
from datetime import datetime, timezone
import json
import math
import re
import ssl
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

HOST = 'health.invalid'  # Public release includes no health service integration.
POLL_SECONDS = 15
STALE_SECONDS = 90
MAX_REPLY = 65536
SLEEP_CODES = {'1': 'Awake', '8': 'Light sleep', '15': 'Deep sleep'}


def private_widget_url(value):
    """An exact user-approved HTTPS host and read-only share route, never arbitrary URLs."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 for c in value):
        raise ValueError('Invalid private widget address')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.hostname != HOST or parsed.netloc not in (HOST, HOST + ':443') or
            parsed.username or parsed.password or parsed.fragment):
        raise ValueError('Unapproved widget origin')
    match = re.fullmatch(r'/share/([A-Za-z0-9_-]{20,256})(?:/api/widget)?/?', parsed.path)
    if not match:
        raise ValueError('Read-only widget path required')
    query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=4)
    if set(query) - {'hours', 'device'} or any(len(v) != 1 for v in query.values()):
        raise ValueError('Unsupported widget query')
    args = {'hours': '1'}
    if 'device' in query:
        device = query['device'][0]
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', device):
            raise ValueError('Invalid device selector')
        args['device'] = device
    return urlunsplit(('https', HOST, '/share/' + match.group(1) + '/api/widget', urlencode(args), ''))


def empty_snapshot(availability='unavailable'):
    return {'schema': 1, 'availability': availability, 'oxygen': None, 'heart_rate': None,
            'sleep': 'Unknown', 'updated_at': None}


def timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Missing source timestamp')
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise ValueError('Source timezone required')
    return moment.timestamp()


def number(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        value = float(value)
    except (ValueError, OverflowError):
        return None
    return round(value) if math.isfinite(value) and 1 <= value <= maximum else None


def age_snapshot(snapshot, now=None):
    now = time.time() if now is None else now
    result = dict(snapshot)
    if result.get('availability') != 'live':
        return result
    try:
        age = now - timestamp(result['updated_at'])
    except (ValueError, TypeError, OverflowError):
        return empty_snapshot()
    if age < -10 or age > STALE_SECONDS:
        result.update(availability='stale', oxygen=None, heart_rate=None, sleep='Unknown')
    return result


def normalize_widget(value, now=None):
    if not isinstance(value, dict):
        return empty_snapshot()
    try:
        moment = timestamp(value.get('updated_at'))
    except (ValueError, TypeError, OverflowError):
        return empty_snapshot()
    updated = datetime.fromtimestamp(moment, timezone.utc).isoformat()
    if value.get('sock_reporting') is not True or value.get('charging') is True:
        result = empty_snapshot('offline')
        result['updated_at'] = updated
        return result
    code = value.get('sleep_state')
    sleep = SLEEP_CODES.get(str(code), 'Unknown')
    if code is None:
        # Accept only an explicit known source label, never infer sleep from vitals.
        label = str(value.get('sleep_state_label', '')).lower().replace('_', ' ').strip()
        sleep = {'awake': 'Awake', 'light sleep': 'Light sleep', 'deep sleep': 'Deep sleep'}.get(label, 'Unknown')
    result = {'schema': 1, 'availability': 'live', 'oxygen': number(value.get('oxygen_now'), 100),
              'heart_rate': number(value.get('heart_rate'), 400), 'sleep': sleep, 'updated_at': updated}
    if result['oxygen'] is None and result['heart_rate'] is None:
        result.update(availability='offline', sleep='Unknown')
    return age_snapshot(result, now)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def fetch_snapshot(address):
    address = private_widget_url(address)
    opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context()))
    # No cookies, account credentials, vendor fallback, proxy or redirects.
    request = Request(address, headers={'Accept': 'application/json', 'Cache-Control': 'no-store'})
    with opener.open(request, timeout=6) as reply:
        if reply.status != 200 or reply.headers.get_content_type() != 'application/json':
            raise ValueError('Unexpected widget response')
        raw = reply.read(MAX_REPLY + 1)
    if len(raw) > MAX_REPLY:
        raise ValueError('Widget response too large')
    return normalize_widget(json.loads(raw))


class StatusPoller:
    """One bounded in-memory snapshot. Construction/idle never opens the network."""
    def __init__(self, load_address, fetch=fetch_snapshot):
        self.load_address, self.fetch = load_address, fetch
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.thread = None
        self.value = empty_snapshot('loading')

    def start(self):
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.run, name='Owlet read-only overlay', daemon=True)
        self.thread.start()

    def run(self):
        try:
            address = private_widget_url(self.load_address())
        except Exception:
            with self.lock:
                self.value = empty_snapshot('unconfigured')
            return
        while not self.closed.is_set():
            try:
                value = self.fetch(address)
            except Exception:
                # Exception text/HTTP errors contain the private URL; never retain/log them.
                value = empty_snapshot()
            with self.lock:
                self.value = value if not self.closed.is_set() else empty_snapshot()
            if self.closed.wait(POLL_SECONDS):
                break

    def snapshot(self):
        with self.lock:
            return age_snapshot(self.value)

    def stop(self):
        self.closed.set()
        with self.lock:
            self.value = empty_snapshot()


def display_parts(value):
    value = age_snapshot(value)
    live = value.get('availability') == 'live'
    oxygen = str(value['oxygen']) + '%' if live and value.get('oxygen') is not None else '—'
    heart = str(value['heart_rate']) + ' bpm' if live and value.get('heart_rate') is not None else '—'
    status = value.get('sleep', 'Unknown') if live else {
        'loading': 'Loading', 'stale': 'Stale', 'offline': 'Sock offline',
        'unconfigured': 'Not configured'}.get(value.get('availability'), 'Unavailable')
    return ('O₂ ' + oxygen, 'HR ' + heart, str(status))
