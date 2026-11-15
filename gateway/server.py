"""Bounded TLS API; paired mTLS required for all camera access. No CORS/proxy."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import queue
import secrets
import socket
import ssl
import threading
from urllib.parse import parse_qs, urlsplit

from gateway.media import MediaSession, StreamBacklogError
from gateway.shared_source import SharedSources, MovementLease
from gateway.health import make_poller, phone_snapshot

MAX_PHONE_VIEWS = 4


class HeaderReader:
    def __init__(self, source):
        self.source, self.remaining = source, 16384

    def readline(self, limit=-1):
        if self.remaining <= 0:
            raise ValueError('Headers too large')
        data = self.source.readline(min(4097, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0 or len(data) > 4096:
            raise ValueError('Headers too large')
        return data


class GatewayState:
    """One identity, viewer limit and source pool across LAN/remote listeners."""
    def __init__(self, identity, source_factory=None):
        self.identity = identity
        self.slots = threading.BoundedSemaphore(16)
        self.view_lock = threading.Lock()
        self.closing = threading.Event()
        self.views = {}
        self.sources = SharedSources(source_factory) if source_factory is not None else SharedSources()
        self.movement = MovementLease()
        self.health = None
        self.health_factory = make_poller

    def stop_unused_health(self):
        # Caller holds view_lock. One poller is shared by both listeners/phones.
        if self.health is not None and not any(not v.stop.is_set() and not v.finished.is_set() for v in self.views.values()):
            self.health.stop()
            self.health = None


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16
    allow_reuse_address = True

    def __init__(self, address, identity, media_factory=MediaSession, state=None, *, allow_pairing=True, source_factory=None):
        self.identity, self.media_factory = identity, media_factory
        self.allow_pairing = allow_pairing
        self.state = state if state is not None else GatewayState(identity, source_factory)
        if self.state.identity is not identity:
            raise ValueError('Listeners must share the same home identity')
        if source_factory is not None and self.state.sources.factory is not source_factory:
            raise ValueError('Listeners must share the same input factory')
        self.tls = identity.tls_context(require_client=not allow_pairing)
        self.slots = self.state.slots
        self.view_lock = self.state.view_lock
        self.closing = self.state.closing
        super().__init__(address, GatewayHandler)

    @property
    def view(self):
        # Legacy synthetic fixture convenience; authorization always uses views.
        return next(iter(self.state.views.values()), None)

    @property
    def view_id(self):
        return next(iter(self.state.views), None)

    def get_request(self):
        raw, address = self.socket.accept()
        raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        raw.settimeout(5)
        return self.tls.wrap_socket(raw, server_side=True, do_handshake_on_connect=False), address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            request.do_handshake()
            super().process_request_thread(request, client_address)
        except (ssl.SSLError, OSError) as exc:
            self.handshake_error(exc)
            self.shutdown_request(request)
        finally:
            self.slots.release()

    def handshake_error(self, exception):
        pass

    def handle_error(self, request, client_address):
        # Never print certificate, URL, request body, pairing code or traceback.
        pass

    def open_view(self, owner, quality):
        with self.view_lock:
            if self.closing.is_set():
                raise PermissionError('Connector stopped')
            for key, prior in list(self.state.views.items()):
                if prior.finished.is_set() and not prior.thread.is_alive():
                    del self.state.views[key]
            if len(self.state.views) >= MAX_PHONE_VIEWS or any(view.owner == owner for view in self.state.views.values()):
                raise BlockingIOError('Phone viewer limit reached or this phone is already viewing')
            view = self.media_factory(owner, quality)
            if isinstance(view, MediaSession) and view.source_factory is None:
                view.source_factory = lambda: self.state.sources.subscribe(quality, view.stop)
            if isinstance(view, MediaSession):
                view.movement_lease = self.state.movement
            view_id = secrets.token_hex(16)
            self.state.views[view_id] = view
            try:
                view.thread.start()
            except Exception:
                del self.state.views[view_id]
                raise
            return view, view_id

    def close_view(self, view):
        view.close()
        with self.view_lock:
            if not view.thread.is_alive():
                for key, current in list(self.state.views.items()):
                    if current is view:
                        del self.state.views[key]
            self.state.stop_unused_health()

    def health_snapshot(self, view):
        with self.view_lock:
            if self.closing.is_set() or view.stop.is_set() or view.finished.is_set():
                raise PermissionError('Viewing session closed')
            if self.state.health is None:
                self.state.health = self.state.health_factory()
                self.state.health.start()
            return phone_snapshot(self.state.health.snapshot())

    def server_close(self):
        with self.view_lock:
            self.closing.set()
            views = list(self.state.views.values())
            for view in views:
                view.stop.set()
            self.state.stop_unused_health()
        for view in views:
            self.close_view(view)
        self.state.sources.close()
        super().server_close()


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'PrivateHomeConnector/0.1'
    sys_version = ''

    def log_message(self, *_args):
        pass

    def parse_request(self):
        original = self.rfile
        try:
            if len(self.raw_requestline) > 4096:
                return False
            self.rfile = HeaderReader(original)
            return super().parse_request()
        except (ValueError, OSError):
            self.close_connection = True
            return False
        finally:
            self.rfile = original

    def send_error(self, code, message=None, explain=None):
        self.reply(code, {'error': 'Request rejected'})

    def reply(self, code, value):
        data = json.dumps(value, separators=(',', ':')).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def check(self, pairing=False):
        if self.server.closing.is_set():
            raise PermissionError('Connector stopped')
        if (self.headers.get('X-Arenti-Client') != 'android-v1' or self.headers.get('Origin')
                or self.headers.get('Sec-Fetch-Site') or self.headers.get('Transfer-Encoding')
                or self.headers.get('Expect') or self.path.startswith(('http:', 'https:', '//'))
                or '#' in self.path or len(self.headers.get_all('Host', [])) != 1):
            raise PermissionError('Native client request required')
        owner = self.server.identity.authenticate(self.connection.getpeercert(binary_form=True))
        if not pairing and not owner:
            raise PermissionError('An enrolled client certificate is required')
        if (owner and self.server.identity.is_family_bootstrap(owner)
                and self.path not in ('/v1/status', '/v1/family/activate')):
            raise PermissionError('Family credential must activate an individual phone first')
        return owner

    def body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not lengths[0].isdigit() or not 1 <= int(lengths[0]) <= 8192:
            raise ValueError('Bounded JSON body required')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('JSON content type required')
        data = self.rfile.read(int(lengths[0]))
        if len(data) != int(lengths[0]):
            raise ValueError('Incomplete body')
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError('Object required')
        return value

    def active_view(self, owner, view_id):
        with self.server.view_lock:
            view = self.server.state.views.get(view_id) if isinstance(view_id, str) else None
            if (not view or view.owner != owner or not isinstance(view_id, str)
                    or view.stop.is_set() or view.finished.is_set()):
                raise PermissionError('Wrong or closed viewing session')
            return view

    def do_GET(self):
        try:
            owner = self.check()
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2) if parsed.query else {}
            if parsed.path == '/v1/status' and not query:
                return self.reply(200, {'protocol': 1, 'gatewayId': self.server.identity.value['gatewayId'],
                    'cameraTransport': 'local-rtsp', 'sensorReadings': False, 'talkback': False,
                    'remoteUrl': self.server.identity.value['remoteUrl'],
                    'maxPhoneViewers': MAX_PHONE_VIEWS,
                    'owletReadings': False,
                    'remoteConfigured': bool(self.server.identity.value['remoteUrl'])})
            if parsed.path == '/v1/capabilities' and set(query) == {'view'} and len(query['view']) == 1:
                return self.reply(200, self.active_view(owner, query['view'][0]).capabilities())
            if parsed.path == '/v1/owlet' and set(query) == {'view'} and len(query['view']) == 1:
                view = self.active_view(owner, query['view'][0])
                return self.reply(200, self.server.health_snapshot(view))
            if parsed.path == '/v1/live.mp4' and set(query) == {'quality'} and query['quality'] in (['high'], ['low']):
                return self.stream(owner, query['quality'][0])
            self.reply(404, {'error': 'Unknown endpoint'})
        except PermissionError:
            self.close_connection = True
            self.reply(401, {'error': 'Client or session not authorized'})
        except BlockingIOError:
            self.reply(409, {'error': 'Phone viewer limit reached or this phone is already viewing'})
        except (BrokenPipeError, ConnectionError, socket.timeout):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.reply(400, {'error': 'Request unavailable'})

    def stream(self, owner, quality):
        view, view_id = self.server.open_view(owner, quality)
        try:
            if not view.ready.wait(12):
                return self.reply(503, {'error': 'Camera startup timed out'})
            try:
                first = view.take(timeout=0.5)
            except (queue.Empty, StreamBacklogError):
                return self.reply(503, {'error': 'Local camera stream unavailable'})
            self.send_response(200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Transfer-Encoding', 'chunked')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-View-Session', view_id)
            age = view.stream_clock_age_ms()
            if isinstance(age, int) and 0 <= age <= 60000:
                self.send_header('X-Stream-Clock-Age-Ms', str(age))
            self.end_headers()
            self.close_connection = True
            data = first
            while not view.stop.is_set() and not self.server.closing.is_set():
                if data is None:
                    try:
                        data = view.take(timeout=0.5)
                    except queue.Empty:
                        if view.finished.is_set():
                            break
                        continue
                    except StreamBacklogError:
                        break
                # Revocation applies to an already-open stream too.
                if not self.server.identity.authenticate(self.connection.getpeercert(binary_form=True)):
                    break
                self.wfile.write(('%x\r\n' % len(data)).encode() + data + b'\r\n')
                self.wfile.flush()
                data = None
            self.wfile.write(b'0\r\n\r\n')
        finally:
            self.server.close_view(view)

    def do_POST(self):
        try:
            pairing = self.path == '/v1/pair'
            if pairing and not self.server.allow_pairing:
                raise PermissionError('Enrollment is LAN-only')
            owner = self.check(pairing=pairing)
            value = self.body()
            if pairing and set(value) == {'token', 'csr', 'name'}:
                return self.reply(200, self.server.identity.enroll(value['token'], value['csr'], value['name']))
            if self.path == '/v1/family/activate' and set(value) == {'csr', 'name'}:
                return self.reply(200, self.server.identity.activate_family(owner, value['csr'], value['name']))
            if self.path == '/v1/ptz' and set(value) == {'view', 'action', 'sequence'}:
                view = self.active_view(owner, value['view'])
                return self.reply(200, view.command(value['action'], value['sequence']))
            if self.path == '/v1/stop' and set(value) == {'view'}:
                view = self.active_view(owner, value['view'])
                view.stop.set()
                view.stop_movement()
                with self.server.view_lock:
                    self.server.state.stop_unused_health()
                return self.reply(200, {'stopped': True})
            self.reply(404, {'error': 'Unknown endpoint'})
        except PermissionError:
            self.close_connection = True
            self.reply(401, {'error': 'Client, pairing or session not authorized'})
        except (BrokenPipeError, ConnectionError, socket.timeout):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            self.reply(400, {'error': 'Request rejected'})

    def do_OPTIONS(self):
        self.reply(405, {'error': 'Browser access is not supported'})
