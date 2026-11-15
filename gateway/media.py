"""One on-demand, bounded, in-memory H.264/AAC stream. No vendor mode or files."""
from fractions import Fraction
import queue
import threading
import time

from browser_stream import FragmentSink
from desktop_profiles import stream_choice
from lan_tools import verify_local_hash
from onvif_local import authenticated_uri, local_url
from ptz_control import PtzController, validate_command
import protected_store

RTSP_OPTIONS = {'rtsp_transport': 'tcp', 'rw_timeout': '5000000',
                'fflags': 'nobuffer', 'flags': 'low_delay'}
MP4_OPTIONS = {'movflags': 'frag_keyframe+empty_moov+default_base_moof',
               'frag_duration': '250000', 'max_interleave_delta': '250000', 'flush_packets': '1'}
MAX_QUEUED_AGE_SECONDS = 2.0


class StreamBacklogError(BufferError):
    """Restart a whole live session, never discard arbitrary H.264/MP4 fragments."""


def open_camera_source(quality):
    import av
    credentials = protected_store.load()
    if credentials.get('state') != 'verified':
        raise ValueError('Verified local camera configuration required')
    verify_local_hash(credentials['cameraSha256'])
    uri, _ = stream_choice(credentials, quality)
    local_url(uri, schemes=('rtsp',))
    return av.open(authenticated_uri(uri, credentials['username'], credentials['password']),
        timeout=(5, 5), options=dict(RTSP_OPTIONS))


class MediaSession:
    def __init__(self, owner, quality, source_factory=None):
        if quality not in ('high', 'low'):
            raise ValueError('Unknown quality')
        self.owner, self.quality, self.source_factory = owner, quality, source_factory
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.queue = queue.Queue(maxsize=128)
        self.lock = threading.RLock()
        self.bytes_queued = 0
        self.video_frames = self.audio_frames = 0
        self.last_video = 0
        self.video_clock_origin = None
        self.error_class = None
        self.ptz = None
        self.movement_lease = None
        self.hold_started = None
        self.last_command_sequence = 0
        self.thread = threading.Thread(target=self.run, daemon=True, name='private-camera-mobile')

    def offer(self, data):
        with self.lock:
            if self.stop.is_set():
                raise InterruptedError('Stream stopped')
            if self.bytes_queued + len(data) > 2 * 1024 * 1024 or self.queue.full():
                raise BufferError('Slow stream consumer')
            self.queue.put_nowait((bytes(data), time.monotonic()))
            self.bytes_queued += len(data)

    def take(self, timeout=0.5):
        data, queued_at = self.queue.get(timeout=timeout)
        with self.lock:
            self.bytes_queued -= len(data)
            if time.monotonic() - queued_at > MAX_QUEUED_AGE_SECONDS:
                self.stop.set()
                raise StreamBacklogError('Live queue exceeded age limit')
        return data

    def stream_clock_age_ms(self):
        """Time since Windows read the first video DTS; NOT camera capture age."""
        with self.lock:
            if self.video_clock_origin is None:
                return None
            return max(0, round((time.monotonic() - self.video_clock_origin) * 1000))

    def open_source(self):
        if self.source_factory:
            return self.source_factory()
        return open_camera_source(self.quality)

    def run(self):
        import av
        av.logging.set_level(av.logging.PANIC)
        sink = FragmentSink(lambda _codecs: self.ready.set(), self.offer)
        try:
            with self.open_source() as source:
                if not source.streams.video or source.streams.video[0].codec_context.name != 'h264':
                    raise ValueError('Mobile connector currently requires H.264')
                template = source.streams.video[0]
                with av.open(sink, 'w', format='mp4', options=dict(MP4_OPTIONS)) as output:
                    video = output.add_stream_from_template(template)
                    video.metadata.clear()
                    audio = output.add_stream('aac', rate=48000) if source.streams.audio else None
                    if audio:
                        audio.layout, audio.bit_rate = 'mono', 64000
                    resampler = av.AudioResampler(format='fltp', layout='mono', rate=48000) if audio else None
                    origin = None
                    next_audio = None
                    for packet in source.demux():
                        if self.stop.is_set():
                            break
                        if packet.stream == template and packet.size:
                            if packet.pts is None or packet.dts is None:
                                continue
                            if origin is None:
                                if not packet.is_keyframe:
                                    continue
                                origin = packet.dts * Fraction(packet.time_base)
                                with self.lock:
                                    self.video_clock_origin = getattr(source, 'last_read_at', None) or time.monotonic()
                            copied = av.Packet(bytes(packet))
                            copied.is_keyframe = packet.is_keyframe
                            copied.pts = packet.pts - round(origin / packet.time_base)
                            copied.dts = packet.dts - round(origin / packet.time_base)
                            copied.duration, copied.time_base, copied.stream = packet.duration, packet.time_base, video
                            output.mux(copied)
                            self.video_frames += 1
                            self.last_video = time.monotonic()
                        elif audio and origin is not None and packet.stream.type == 'audio' and packet.size:
                            decoder = getattr(source, 'decode_audio', None)
                            for decoded in (decoder(packet) if decoder else packet.decode()):
                                for frame in resampler.resample(decoded):
                                    if frame.pts is None:
                                        continue
                                    timestamp = frame.pts * Fraction(frame.time_base) - origin
                                    if timestamp < 0:
                                        continue
                                    proposed = round(timestamp * 48000)
                                    frame.pts = proposed if next_audio is None else max(proposed, next_audio)
                                    frame.time_base = Fraction(1, 48000)
                                    next_audio = frame.pts + frame.samples
                                    for encoded in audio.encode(frame):
                                        output.mux(encoded)
                                        self.audio_frames += 1
                    if audio and not self.stop.is_set():
                        for encoded in audio.encode(None):
                            output.mux(encoded)
        except Exception as exc:
            self.error_class = type(exc).__name__
            if isinstance(exc, BufferError):
                self.stop.set()  # Do not drain seconds of already-obsolete media.
        finally:
            self.finished.set()
            self.ready.set()
            self.stop_movement()

    def capabilities(self):
        with self.lock:
            if not self.ptz:
                self.ptz = PtzController()
            return self.ptz.prepare()

    def command(self, action, sequence):
        validate_command(action, sequence)
        with self.lock:
            # Record even a non-owner Stop/busy command, so an older in-flight
            # direction cannot become valid after another phone releases control.
            if sequence <= self.last_command_sequence:
                return {'accepted': False, 'reason': 'stale', 'cloudRequests': 0}
            self.last_command_sequence = sequence
            if self.movement_lease is not None:
                return self.movement_lease.command(self, action, lambda: self._command(action, sequence))
            return self._command(action, sequence)

    def _command(self, action, sequence):
        with self.lock:
            if self.stop.is_set() or self.finished.is_set() or not self.ptz:
                raise ValueError('No prepared viewing session')
            now = time.monotonic()
            if action != 'stop' and now - self.last_video > 1.2:
                self.ptz.stop_active()
                raise ValueError('Live video is stale')
            if action == 'stop':
                self.hold_started = None
            else:
                if not self.ptz.moving or self.hold_started is None:
                    self.hold_started = now
                if now - self.hold_started >= 3:
                    self.ptz.stop_active()
                    raise ValueError('Movement hold limit reached')
            return self.ptz.command(action, sequence)

    def stop_movement(self):
        with self.lock:
            if self.movement_lease is not None:
                self.movement_lease.close(self, self._close_ptz)
            else:
                self._close_ptz()

    def _close_ptz(self):
        if self.ptz:
            try:
                self.ptz.close()
            except Exception:
                pass

    def close(self):
        self.stop.set()
        self.stop_movement()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
