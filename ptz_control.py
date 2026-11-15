"""Bounded owner-requested local ONVIF pan/tilt with camera and server leases."""
import re
import threading
import time
from xml.sax.saxutils import escape

from lan_tools import verify_local_hash
from onvif_local import DEVICE, MEDIA, SCHEMA, SOAP, OnvifClient, local_url
import protected_store

PTZ = 'http://www.onvif.org/ver20/ptz/wsdl'
DIRECTIONS = {'left': (-0.2, 0), 'right': (0.2, 0), 'up': (0, 0.2), 'down': (0, -0.2)}


class PtzClient(OnvifClient):
    allowed_actions = OnvifClient.allowed_actions | {'GetConfigurationOptions', 'GetStatus', 'ContinuousMove', 'Stop'}


def duration_seconds(text):
    match = re.fullmatch(r'PT(\d+(?:\.\d+)?)S', text or '')
    if not match:
        raise ValueError('Unsupported PTZ duration')
    return float(match.group(1))


def validate_command(action, sequence):
    if action not in (*DIRECTIONS, 'stop') or type(sequence) is not int or not 1 <= sequence <= 2147483647:
        raise ValueError('Invalid bounded PTZ command')


class PtzController:
    def __init__(self):
        credentials = protected_store.load()
        if credentials.get('state') != 'verified':
            raise ValueError('Verified local credentials required')
        verify_local_hash(credentials['cameraSha256'])
        self.client = PtzClient(credentials['username'], credentials['password'])
        self.client.request_timeout = (1, 1.5)
        self.lock = threading.RLock()
        self.timer = None
        self.lease = 0
        self.sequence = 0
        self.closed = False
        self.last_move = 0.0
        self.moving = False
        self.device_url = credentials['deviceUrl']
        self.url = self.profile = None
        self.directions = []
        self.camera_timeout = 0.35
        # Identity-gated to the D3: its standard Stop action is unsupported.
        self.stop_method = 'ContinuousMove-zero'

    def _call(self, url, namespace, action, inner=''):
        code, root = self.client.soap(url, namespace, action, inner)
        if code != 200 or root is None or root.find(f'.//{{{SOAP}}}Fault') is not None:
            raise RuntimeError('PTZ request not accepted')
        return root

    def prepare(self):
        with self.lock:
            if self.closed:
                raise RuntimeError('PTZ controller closed')
            if self.url:
                return self.status()
            self.client.synchronize_clock(self.device_url)
            caps = self._call(self.device_url, DEVICE, 'GetCapabilities', '<t:Category>All</t:Category>')
            ptz_url = caps.findtext(f'.//{{{SCHEMA}}}PTZ/{{{SCHEMA}}}XAddr')
            media_url = caps.findtext(f'.//{{{SCHEMA}}}Media/{{{SCHEMA}}}XAddr')
            local_url(ptz_url)
            local_url(media_url)
            root = self._call(media_url, MEDIA, 'GetProfiles')
            profiles = [p for p in root.findall(f'.//{{{MEDIA}}}Profiles')
                        if p.find(f'{{{SCHEMA}}}PTZConfiguration') is not None]
            if not profiles:
                raise RuntimeError('No PTZ profile')
            def score(profile):
                return int(profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Width', '0')) * int(profile.findtext(f'.//{{{SCHEMA}}}Resolution/{{{SCHEMA}}}Height', '0'))
            profile = max(profiles, key=score)
            token = profile.get('token')
            configuration = profile.find(f'{{{SCHEMA}}}PTZConfiguration').get('token')
            if not token or not configuration:
                raise RuntimeError('PTZ tokens unavailable')
            options = self._call(ptz_url, PTZ, 'GetConfigurationOptions',
                                 f'<t:ConfigurationToken>{escape(configuration)}</t:ConfigurationToken>')
            space = options.find(f'.//{{{SCHEMA}}}ContinuousPanTiltVelocitySpace')
            if space is None:
                raise RuntimeError('Bounded continuous pan/tilt unsupported')
            ranges = {}
            for axis in ('X', 'Y'):
                low = float(space.findtext(f'{{{SCHEMA}}}{axis}Range/{{{SCHEMA}}}Min'))
                high = float(space.findtext(f'{{{SCHEMA}}}{axis}Range/{{{SCHEMA}}}Max'))
                ranges[axis] = (low, high)
            self.directions = [name for name, (x, y) in DIRECTIONS.items()
                               if ranges['X'][0] <= x <= ranges['X'][1] and ranges['Y'][0] <= y <= ranges['Y'][1]]
            if not self.directions:
                raise RuntimeError('No safe pan/tilt velocity range')
            minimum = options.findtext(f'.//{{{SCHEMA}}}PTZTimeout/{{{SCHEMA}}}Min')
            maximum = options.findtext(f'.//{{{SCHEMA}}}PTZTimeout/{{{SCHEMA}}}Max')
            low = duration_seconds(minimum) if minimum else 0.05
            high = duration_seconds(maximum) if maximum else 1
            self.camera_timeout = max(self.camera_timeout, low)
            if not 0.05 <= low <= self.camera_timeout <= min(high, 1):
                raise RuntimeError('Camera PTZ lease cannot be made sufficiently short')
            self.url, self.profile = ptz_url, token
            return self.status()

    def status(self):
        return {'supported': bool(self.url), 'directions': self.directions,
                'cameraTimeoutSeconds': self.camera_timeout,
                'serverStopSeconds': 0.45, 'lastSequence': self.sequence,
                'moving': self.moving, 'cloudRequests': 0,
                'stopMethod': getattr(self, 'stop_method', 'Stop')}

    def command(self, action, sequence):
        validate_command(action, sequence)
        with self.lock:
            if self.closed:
                raise RuntimeError('PTZ controller closed')
            if sequence <= self.sequence:
                return {'accepted': False, 'reason': 'stale', 'cloudRequests': 0}
            self.sequence = sequence
            if action == 'stop':
                self._stop()
                return {'accepted': True, 'moving': False, 'cloudRequests': 0}
            if action not in self.directions or not self.url:
                raise ValueError('Direction unsupported')
            now = time.monotonic()
            if now - self.last_move < 0.15:
                return {'accepted': False, 'reason': 'rate_limit', 'cloudRequests': 0}
            self.lease += 1
            lease = self.lease
            if self.timer:
                self.timer.cancel()
            x, y = DIRECTIONS[action]
            inner = (f'<t:ProfileToken>{escape(self.profile)}</t:ProfileToken>'
                     f'<t:Velocity><tt:PanTilt x="{x}" y="{y}"/></t:Velocity>'
                     f'<t:Timeout>PT{self.camera_timeout:g}S</t:Timeout>')
            self.moving = True
            self.last_move = now
            # Arm before network I/O: camera Timeout remains the final backstop.
            self.timer = threading.Timer(0.45, self._lease_expired, args=(lease,))
            self.timer.daemon = True
            self.timer.start()
            try:
                self._call(self.url, PTZ, 'ContinuousMove', inner)
            except Exception:
                self._stop()
                raise
            return {'accepted': True, 'moving': True, 'cloudRequests': 0}

    def _lease_expired(self, lease):
        with self.lock:
            if not self.closed and lease == self.lease:
                try:
                    self._stop()
                except Exception:
                    # Do not report a confirmed stop after both stop methods
                    # fail. close() will retry; camera timeout is requested but
                    # physical enforcement cannot be assumed on this firmware.
                    self.stop_error = True

    def _stop(self):
        self.lease += 1
        if self.timer:
            self.timer.cancel()
            self.timer = None
        if self.url:
            inner = (f'<t:ProfileToken>{escape(self.profile)}</t:ProfileToken>'
                     '<t:PanTilt>true</t:PanTilt><t:Zoom>false</t:Zoom>')
            if getattr(self, 'stop_method', 'Stop') == 'Stop':
                try:
                    self._call(self.url, PTZ, 'Stop', inner)
                except Exception:
                    # This D3 advertises PTZ but returns ActionNotSupported for
                    # Stop. ContinuousMove at zero velocity is a stop, not a
                    # directional action, and is supported by its firmware.
                    self._zero_velocity_stop()
                    self.stop_method = 'ContinuousMove-zero'
            else:
                self._zero_velocity_stop()
        self.moving = False

    def _zero_velocity_stop(self):
        inner = (f'<t:ProfileToken>{escape(self.profile)}</t:ProfileToken>'
                 '<t:Velocity><tt:PanTilt x="0" y="0"/></t:Velocity>'
                 f'<t:Timeout>PT{self.camera_timeout:g}S</t:Timeout>')
        self._call(self.url, PTZ, 'ContinuousMove', inner)

    def stop_active(self):
        with self.lock:
            if not self.closed and (self.moving or self.timer):
                self.sequence += 1
                self._stop()

    def close(self):
        with self.lock:
            try:
                if self.moving or self.timer:
                    self._stop()
            finally:
                self.closed = True
                self.client.close()
