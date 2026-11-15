"""Bounded synthetic-only loopback gateway for the owned Android test emulator.

No real camera/account reads. Only synthetic credentials are generated for debug
and androidTest; never use these assets in a release APK.
"""
import argparse
import io
import json
import os
import random
from pathlib import Path
from fractions import Fraction
import select
import socket
import socketserver
import tempfile
import threading
import time

from gateway.identity import Identity, checked_funnel_url
from gateway.media import MediaSession
from gateway.server import GatewayServer, GatewayHandler


def synthetic_source(quality='low'):
    """Four minutes of generated frames/silence, long enough for Auto's stability hold."""
    import av
    data = io.BytesIO()
    with av.open(data, mode='w', format='mp4') as output:
        video = output.add_stream('libx264', rate=10)
        width, height = (160, 120) if quality == 'high' else (64, 48)
        video.width, video.height, video.pix_fmt = width, height, 'yuv420p'
        video.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        video.codec_context.gop_size = 10
        video.codec_context.thread_count = 1
        audio = output.add_stream('aac', rate=48000)
        audio.layout = 'mono'
        random_pixels = random.Random(1234)
        for index in range(2400):
            frame = av.VideoFrame(width, height, 'yuv420p')
            for number, plane in enumerate(frame.planes):
                plane.update(random_pixels.randbytes(plane.buffer_size) if quality == 'high' and number == 0
                             else bytes([32 + index % 160]) * plane.buffer_size)
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame): output.mux(packet)
            sound = av.AudioFrame(format='fltp', layout='mono', samples=4800)
            sound.planes[0].update(bytes(sound.planes[0].buffer_size))
            sound.sample_rate, sound.pts, sound.time_base = 48000, index * 4800, Fraction(1, 48000)
            for packet in audio.encode(sound): output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None): output.mux(packet)
    return data.getvalue()


class PacedInput:
    """Generated in-memory input, shared by all views of one fixture quality."""
    def __init__(self, data, stop, audio_gap_until=lambda: 0):
        import av
        self.stop, self.audio_gap_until = stop, audio_gap_until
        self.media = av.open(io.BytesIO(data))
        self.streams = self.media.streams

    def __enter__(self): return self
    def __exit__(self, *args): self.media.close()

    def demux(self):
        started = time.monotonic()
        for packet in self.media.demux():
            timestamp = float(packet.dts * packet.time_base) if packet.dts is not None else 0
            if self.stop.wait(max(0, started + timestamp - time.monotonic())): return
            if packet.stream.type == 'audio' and time.monotonic() < self.audio_gap_until(): continue
            yield packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic-only', action='store_true', required=True)
    parser.add_argument('--seconds', type=int, default=300)
    parser.add_argument('--port', type=int, default=17443)
    parser.add_argument('--funnel-url', default='', help='Owner-configured public staging Funnel -> loopback 17446')
    args = parser.parse_args()
    if not 10 <= args.seconds <= 600 or args.port not in (17443, 17444):
        raise ValueError('Unexpected synthetic fixture bounds')
    if args.funnel_url:
        checked_funnel_url(args.funnel_url)
    approved = Path(os.environ['LOCALAPPDATA']) / 'Temp' / 'opencode'
    assets = Path(__file__).parent / 'android' / 'app' / 'build' / 'generated' / 'fixtureAssets'
    assets.mkdir(parents=True, exist_ok=True)
    asset = assets / 'pairing-fixture.json'
    family_asset = assets / 'family-fixture.json'
    debug_assets = assets.parent / 'familyFixtureAssets'
    debug_assets.mkdir(parents=True, exist_ok=True)
    debug_asset = debug_assets / 'home-family-access.json'
    sources = {quality: synthetic_source(quality) for quality in ('high', 'low')}
    statistics = {'starts': 0, 'sourceStarts': 0, 'commands': 0, 'jitterEvents': 0, 'audioGapEvents': 0}
    statistics_lock = threading.Lock()
    fixture_done = threading.Event()
    audio_gap_until = [0.0]
    stream_failures = [0]
    bandwidth = [0]
    health_mode = ['live']

    class SyntheticHealth:
        def start(self): pass
        def stop(self): pass
        def snapshot(self):
            from datetime import datetime, timezone
            from owlet_overlay import normalize_widget
            return normalize_widget({'updated_at': datetime.fromtimestamp(time.time() - (100 if health_mode[0] == 'stale' else 0), timezone.utc).isoformat(),
                'sock_reporting': health_mode[0] != 'offline', 'oxygen_now': 98, 'heart_rate': 120, 'sleep_state': '8'})

    def shared_input(quality):
        with statistics_lock: statistics['sourceStarts'] += 1
        return PacedInput(sources[quality], fixture_done, lambda: audio_gap_until[0])

    class FaultSubscription:
        def __init__(self, session, subscription):
            self.session, self.subscription = session, subscription
        def __enter__(self):
            self.subscription.__enter__()
            return self
        def __exit__(self, *args): self.subscription.__exit__(*args)
        def __getattr__(self, name): return getattr(self.subscription, name)
        def demux(self):
            for packet in self.subscription.demux():
                while not self.session.stop.is_set():
                    if self.session.frozen.is_set():
                        self.session.stop.wait(0.1)
                        continue
                    if time.monotonic() < self.session.jitter_until:
                        self.session.stop.wait(min(0.1, self.session.jitter_until - time.monotonic()))
                        continue
                    yield packet
                    break
                if self.session.stop.is_set(): break

    class SyntheticSession(MediaSession):
        def __init__(self, owner, quality):
            self.frozen = threading.Event()
            self.jitter_until = 0.0
            with statistics_lock:
                statistics['starts'] += 1
                statistics[quality + 'Starts'] = statistics.get(quality + 'Starts', 0) + 1
            super().__init__(owner, quality)
        def open_source(self):
            return FaultSubscription(self, super().open_source())
        def capabilities(self):
            return {'supported': False, 'directions': []}
        def command(self, action, sequence):
            with statistics_lock: statistics['commands'] += 1
            raise ValueError('Synthetic fixture never controls a camera')

    class SyntheticHandler(GatewayHandler):
        def stream(self, owner, quality):
            original = self.wfile
            class ThrottledWriter:
                def write(self, data):
                    # Throttle only synthetic stream responses, equally for both
                    # qualities. No camera, router or production server changes.
                    for offset in range(0, len(data), 1024):
                        chunk = data[offset:offset + 1024]
                        rate = bandwidth[0]
                        if rate and fixture_done.wait(len(chunk) / rate):
                            raise ConnectionAbortedError('Synthetic fixture ended')
                        original.write(chunk)
                    return len(data)
                def flush(self): original.flush()
            self.wfile = ThrottledWriter()
            try: return super().stream(owner, quality)
            finally: self.wfile = original

        def do_GET(self):
            if self.path.startswith('/v1/live.mp4?'):
                try:
                    self.check()
                    with statistics_lock:
                        fail = stream_failures[0] > 0
                        if fail:
                            stream_failures[0] -= 1
                            statistics['streamFailures'] = statistics.get('streamFailures', 0) + 1
                    if fail: return self.reply(503, {'error': 'Synthetic temporary stream failure'})
                except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
            if self.path != '/v1/fixture/state': return super().do_GET()
            try:
                self.check()
                with statistics_lock: value = dict(statistics)
                value['bandwidthBytesPerSecond'] = bandwidth[0]
                value['syntheticHighBytes'] = len(sources['high'])
                value['syntheticLowBytes'] = len(sources['low'])
                with self.server.view_lock:
                    value['active'] = bool(self.server.state.views)
                    value['activeViews'] = len(self.server.state.views)
                return self.reply(200, value)
            except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
        def do_POST(self):
            if self.path == '/v1/fixture/sock':
                try:
                    self.check()
                    body = self.body()
                    if set(body) != {'mode'} or body['mode'] not in ('live', 'offline', 'stale'):
                        raise ValueError('Synthetic sock mode required')
                    health_mode[0] = body['mode']
                    return self.reply(200, {'injected': True})
                except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
            if self.path == '/v1/fixture/bandwidth':
                try:
                    self.check()
                    body = self.body()
                    if set(body) != {'bytesPerSecond'} or type(body['bytesPerSecond']) is not int or body['bytesPerSecond'] not in (0, 16000):
                        raise ValueError('Bounded synthetic bandwidth required')
                    bandwidth[0] = body['bytesPerSecond']
                    return self.reply(200, {'injected': True})
                except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
            if self.path == '/v1/fixture/fail-streams':
                try:
                    self.check()
                    body = self.body()
                    if set(body) != {'count'} or type(body['count']) is not int or not 0 <= body['count'] <= 6:
                        raise ValueError('Bounded synthetic failure count required')
                    with statistics_lock: stream_failures[0] = body['count']
                    return self.reply(200, {'injected': True})
                except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
            if self.path == '/v1/fixture/finish':
                try:
                    self.check()
                    if self.body() != {}: raise ValueError('Unexpected fixture body')
                    self.reply(200, {'finished': True})
                    fixture_done.set()
                    return
                except Exception: return self.reply(401, {'error': 'Synthetic test client required'})
            if self.path not in ('/v1/fixture/freeze', '/v1/fixture/jitter', '/v1/fixture/audio-gap'):
                return super().do_POST()
            try:
                owner = self.check()
                body = self.body()
                if self.path == '/v1/fixture/freeze':
                    if body != {}: raise ValueError('Unexpected fixture body')
                elif set(body) != {'milliseconds'} or type(body['milliseconds']) is not int or not 0 <= body['milliseconds'] <= 8000:
                    raise ValueError('Bounded synthetic pause required')
                with self.server.view_lock:
                    view = next((view for view in self.server.state.views.values() if view.owner == owner), None)
                    if view is None or view.owner != owner: raise PermissionError('Own synthetic view required')
                    if self.path == '/v1/fixture/freeze': view.frozen.set()
                    elif self.path == '/v1/fixture/jitter':
                        view.jitter_until = time.monotonic() + body['milliseconds'] / 1000
                        with statistics_lock: statistics['jitterEvents'] += 1
                    else:
                        audio_gap_until[0] = time.monotonic() + body['milliseconds'] / 1000
                        with statistics_lock: statistics['audioGapEvents'] += int(body['milliseconds'] > 0)
                return self.reply(200, {'frozen': True} if self.path.endswith('/freeze') else {'injected': True})
            except Exception: return self.reply(401, {'error': 'Synthetic test client required'})

    class SyntheticServer(GatewayServer):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.RequestHandlerClass = SyntheticHandler
            self.state.health_factory = SyntheticHealth
        def handshake_error(self, exception):
            print(json.dumps({'syntheticTlsError': type(exception).__name__,
                'reason': getattr(exception, 'reason', None)}), flush=True)

    class OpaqueForwarder(socketserver.BaseRequestHandler):
        def handle(self):
            # Intentionally no SSL context/cert/private key: copy opaque bytes.
            with socket.create_connection(('127.0.0.1', 17446), timeout=5) as upstream:
                self.request.settimeout(5)
                while True:
                    readable, _, _ = select.select([self.request, upstream], [], [], 5)
                    if not readable:
                        return
                    for current in readable:
                        data = current.recv(65536)
                        if not data:
                            return
                        (upstream if current is self.request else self.request).sendall(data)

    class OpaqueRelay(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
        def handle_error(self, request, client_address):
            pass

    with tempfile.TemporaryDirectory(prefix='android-synthetic-gateway-', dir=approved) as temp:
        identity = Identity(Path(temp) / 'private')
        identity.initialize(f'https://10.0.2.2:{args.port}', args.funnel_url or 'https://10.0.2.2:17445',
            funnel=bool(args.funnel_url))
        asset_data = json.dumps(identity.pairing_payload())
        asset.write_text(asset_data, encoding='utf-8')
        family = identity.prepare_family_credential()
        family_data = json.dumps(family)
        family_asset.write_text(family_data, encoding='utf-8')
        # Force the first-launch test through the authenticated remote route,
        # without a prior home-LAN enrollment or QR scan.
        debug_data = json.dumps({**family, 'localUrl': 'https://127.0.0.1:9'})
        debug_asset.write_text(debug_data, encoding='utf-8')
        server = SyntheticServer(('127.0.0.1', args.port), identity, media_factory=SyntheticSession,
                                 source_factory=shared_input)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        remote = SyntheticServer(('127.0.0.1', 17446), identity, media_factory=SyntheticSession,
                                 state=server.state, allow_pairing=False)
        remote_thread = threading.Thread(target=remote.serve_forever, daemon=True)
        remote_thread.start()
        relay = remote if args.funnel_url else OpaqueRelay(('127.0.0.1', 17445), OpaqueForwarder)
        relay_thread = threading.Thread(target=relay.serve_forever, daemon=True)
        if relay is not remote:
            relay_thread.start()
        try:
            fixture_done.wait(args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            if relay is not remote:
                relay.shutdown(); relay.server_close(); relay_thread.join(timeout=2)
            remote.shutdown(); remote.server_close(); remote_thread.join(timeout=2)
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            if asset.exists() and asset.read_text(encoding='utf-8') == asset_data:
                asset.unlink()
            for path, expected in ((family_asset, family_data), (debug_asset, debug_data)):
                if path.exists() and path.read_text(encoding='utf-8') == expected:
                    path.unlink()
    print(json.dumps({'syntheticFixtureStopped': True, 'cameraRequests': 0, 'savedMedia': False}))


if __name__ == '__main__':
    main()
