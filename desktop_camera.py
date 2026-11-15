"""Native player's bounded in-memory media and local-only worker threads.

No browser, HTTP listener, vendor account or settings API. Local capture is opt-in.
Exceptions are reduced to class names: decoder errors can contain RTSP passwords.
"""
import threading
import time

from lan_tools import verify_local_hash
from onvif_local import authenticated_uri, local_url
from ptz_control import DIRECTIONS, PtzController
from desktop_profiles import stream_choice
from desktop_capture import CaptureController
import protected_store

MAX_AUDIO_BYTES = 48000  # half a second, mono s16 at 48 kHz
MAX_FRAME_BYTES = 32 * 1024 * 1024


class MediaMailbox:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.audio = bytearray()
        self.updated = 0.0
        self.stats = {'videoFrames': 0, 'audioFrames': 0, 'audioSamples': 0,
                      'reconnects': 0, 'width': 0, 'height': 0, 'codec': '',
                      'state': 'idle', 'errorClass': None, 'cloudRequests': 0,
                      'mediaSaved': False, 'lastVideoAt': 0.0}

    def status(self, **values):
        with self.lock:
            self.stats.update(values)

    def snapshot(self):
        with self.lock:
            return dict(self.stats)

    def put_video(self, data, width, height, stride, source_width, source_height):
        if len(data) > MAX_FRAME_BYTES or len(data) != stride * height:
            raise ValueError('Invalid display frame size')
        with self.lock:
            self.frame = (data, width, height, stride)
            self.updated = time.monotonic()
            self.stats['videoFrames'] += 1
            self.stats.update(width=source_width, height=source_height, state='live', lastVideoAt=self.updated)

    def put_audio(self, data, samples):
        with self.lock:
            self.audio.extend(data)
            if len(self.audio) > MAX_AUDIO_BYTES:
                del self.audio[:-MAX_AUDIO_BYTES]
            self.stats['audioFrames'] += 1
            self.stats['audioSamples'] += samples

    def take(self, audio_limit=0):
        with self.lock:
            frame, self.frame = self.frame, None
            count = min(max(0, audio_limit), len(self.audio))
            count -= count % 2
            audio = bytes(self.audio[:count])
            del self.audio[:count]
            if audio_limit == 0:
                self.audio.clear()
            return frame, audio

    def recent(self, now=None):
        with self.lock:
            return self.updated > 0 and (time.monotonic() if now is None else now) - self.updated < 1.25

    def discard_audio(self):
        """Clear playback backlog on output change without touching video/capture."""
        with self.lock:
            self.audio.clear()

    def clear(self):
        with self.lock:
            self.frame = None
            self.audio.clear()
            self.updated = 0
            self.stats['lastVideoAt'] = 0.0


class CameraWorker:
    def __init__(self, mailbox, quality='high'):
        self.mailbox = mailbox
        self.quality = quality
        self.stop = threading.Event()
        self.capture = CaptureController(mailbox)
        self.thread = threading.Thread(target=self.run, daemon=True, name='arenti-native-media')

    def start(self):
        self.thread.start()

    def request_stop(self):
        self.stop.set()
        self.capture.stream_ended()

    def start_recording(self, path):
        if self.stop.is_set() or not self.mailbox.recent():
            raise RuntimeError('Live video is required')
        self.capture.start_recording(path)

    def stop_recording(self):
        self.capture.stop_recording()

    def request_snapshot(self, path):
        if self.stop.is_set() or not self.mailbox.recent():
            raise RuntimeError('Live video is required')
        self.capture.request_snapshot(path)

    def alive(self):
        return self.thread.is_alive()

    def run(self):
        import av
        av.logging.set_level(av.logging.PANIC)
        try:
            self.mailbox.status(state='connecting', stage='protected_configuration')
            credentials = protected_store.load()
            if credentials.get('state') != 'verified':
                raise ValueError('Verified local configuration required')
            self.mailbox.status(stage='camera_identity')
            verify_local_hash(credentials['cameraSha256'])
            uri = credentials['rtspUri']
            self.mailbox.status(stage='stream_profiles')
            try:
                uri, profiles = stream_choice(credentials, self.quality)
                self.mailbox.status(profiles=profiles, quality=self.quality)
            except Exception as exc:
                # The protected default remains usable if profile discovery fails.
                # Never pretend a requested low-quality choice actually succeeded.
                if self.quality != 'high':
                    raise
                self.mailbox.status(profileErrorClass=type(exc).__name__, quality='high')
            self.mailbox.status(stage='local_url_check')
            local_url(uri, schemes=('rtsp',))
            address = authenticated_uri(uri, credentials['username'], credentials['password'])
            # The UI owns one bounded retry budget and a decoded-frame watchdog.
            # Each worker owns one source, finalizing capture before replacement.
            if self.stop.is_set():
                return
            self.mailbox.status(stage='rtsp_open')
            with av.open(address, timeout=(5, 5), options={
                'rtsp_transport': 'tcp', 'rw_timeout': '5000000',
                'fflags': 'nobuffer', 'flags': 'low_delay',
            }) as source:
                self.mailbox.status(stage='video_track_check')
                if not source.streams.video:
                    raise ValueError('Camera has no video track')
                video = source.streams.video[0]
                if video.codec_context.name not in ('h264', 'hevc'):
                    raise ValueError('Unsupported camera codec')
                self.mailbox.status(codec=video.codec_context.name, errorClass=None)
                resampler = av.AudioResampler(format='s16', layout='mono', rate=48000)
                self.mailbox.status(stage='decoding')
                for packet in source.demux():
                    if self.stop.is_set():
                        break
                    if packet.stream.type not in ('audio', 'video'):
                        continue
                    if packet.stream == video:
                        self.capture.video_packet(packet, video, bool(source.streams.audio))
                    for frame in packet.decode():
                        if self.stop.is_set():
                            break
                        if packet.stream.type == 'video':
                            if not 1 <= frame.width <= 4096 or not 1 <= frame.height <= 2160:
                                raise ValueError('Camera frame exceeds display bounds')
                            self.capture.video_frame(frame)
                            width = min(frame.width, 1600)
                            height = max(2, round(frame.height * width / frame.width))
                            rgb = frame.reformat(width=width, height=height, format='rgb24')
                            plane = rgb.planes[0]
                            self.mailbox.put_video(bytes(plane), width, height, plane.line_size,
                                                   frame.width, frame.height)
                        else:
                            for audio in resampler.resample(frame):
                                self.capture.audio_frame(audio)
                                self.mailbox.put_audio(bytes(audio.planes[0])[:audio.samples * 2], audio.samples)
                if not self.stop.is_set():
                    raise RuntimeError('Camera stream ended')
        except Exception as exc:
            self.mailbox.status(state='error', errorClass=type(exc).__name__)
        finally:
            self.capture.close()
            if self.stop.is_set():
                self.mailbox.status(state='stopped')
            self.mailbox.clear()


class MovementWorker:
    """One serialized ONVIF owner; release takes priority over future pulses."""
    def __init__(self, mailbox):
        self.mailbox = mailbox
        self.condition = threading.Condition()
        self.direction = None
        self.stop_pending = False
        self.closing = False
        self.ready = False
        self.directions = []
        self.error_class = None
        self.moves = 0
        self.hold_deadline = 0.0
        self.hold_maximum = 0.0
        self.thread = threading.Thread(target=self.run, daemon=True, name='arenti-native-ptz')

    def start(self):
        self.thread.start()

    def alive(self):
        return self.thread.is_alive()

    def hold(self, direction):
        if direction not in DIRECTIONS:
            raise ValueError('Invalid pan/tilt direction')
        with self.condition:
            if not self.ready or self.closing or not self.mailbox.recent() or self.direction:
                return False
            if direction not in self.directions:
                return False
            self.direction = direction
            self.hold_deadline = time.monotonic() + 0.5
            self.hold_maximum = time.monotonic() + 3
            self.condition.notify_all()
            return True

    def renew(self):
        """Only the responsive GUI renews holds; a hung UI must not drive a motor."""
        with self.condition:
            if self.direction and not self.closing:
                self.hold_deadline = min(time.monotonic() + 0.5, self.hold_maximum)

    def release(self, force=False):
        with self.condition:
            if self.direction is not None or force:
                self.stop_pending = True
            self.direction = None
            self.condition.notify_all()

    def request_stop(self):
        with self.condition:
            self.closing = True
            self.direction = None
            self.condition.notify_all()

    def run(self):
        controller = None
        try:
            controller = PtzController()
            capabilities = controller.prepare()
            with self.condition:
                self.directions = capabilities['directions']
                self.ready = not self.closing
            next_pulse = 0
            while True:
                with self.condition:
                    if self.closing:
                        break
                    if self.direction and (not self.mailbox.recent() or time.monotonic() >= self.hold_deadline
                                           or time.monotonic() >= self.hold_maximum):
                        self.direction = None
                        self.stop_pending = True
                    if self.stop_pending:
                        action = 'stop'
                        self.stop_pending = False
                    elif self.direction and time.monotonic() >= next_pulse:
                        action = self.direction
                    else:
                        self.condition.wait(0.05)
                        continue
                result = controller.command(action, controller.sequence + 1)
                if action != 'stop' and result.get('accepted'):
                    self.moves += 1
                next_pulse = time.monotonic() + 0.25
        except Exception as exc:
            self.error_class = type(exc).__name__
        finally:
            with self.condition:
                self.ready = False
                self.direction = None
            if controller is not None:
                try:
                    controller.close()
                except Exception as exc:
                    self.error_class = type(exc).__name__
