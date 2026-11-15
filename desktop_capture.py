"""Explicit local PNG/MP4 capture. No camera connection or automatic capture.

Video packets are copied, not re-encoded; decoded camera audio becomes AAC.
All output happens on separate threads with bounded recording queues. Errors
are reported by class only, never by network/decoder exception text.
"""
from fractions import Fraction
from pathlib import Path
import ctypes
import os
import queue
import shutil
import threading
import time

MAX_QUEUE_BYTES = 16 * 1024 * 1024
MIN_FREE_BYTES = 32 * 1024 * 1024


def local_capture_path(path, suffix):
    path = Path(path).expanduser()
    if not path.is_absolute() or path.suffix.lower() != suffix:
        raise ValueError('An absolute capture path with the required extension is needed')
    # Reject explicit network destinations before resolve()/filesystem probes.
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network capture destinations are not supported')
    if os.name == 'nt' and ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
        raise ValueError('Mapped network drives are not supported')
    path = path.resolve()
    if str(path).startswith(('\\\\', '//')):
        raise ValueError('Network capture destinations are not supported')
    if os.name == 'nt' and ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 4:
        raise ValueError('Mapped network drives are not supported')
    if not path.parent.is_dir():
        raise ValueError('Choose an existing local folder')
    if path.exists():
        raise FileExistsError('Existing files are never overwritten')
    return path


def media_time(pts, time_base):
    return None if pts is None or time_base is None else pts * Fraction(time_base)


def save_png(frame, path):
    """Encode the next full-resolution decoded frame, not a screen/window grab."""
    import av
    path = local_capture_path(path, '.png')
    encoder = av.CodecContext.create('png', 'w')
    encoder.width, encoder.height = frame.width, frame.height
    encoder.pix_fmt = 'rgb24'
    encoder.time_base = Fraction(1, 1)
    rgb = frame.reformat(format='rgb24')
    packets = list(encoder.encode(rgb)) + list(encoder.encode(None))
    data = b''.join(bytes(packet) for packet in packets)
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('PNG encoder did not return an image')
    with path.open('xb') as output:
        output.write(data)


class RecordingSession:
    """Owns only the output file, never the camera/source or PTZ worker."""
    def __init__(self, mailbox, path, video_stream, first_packet, has_audio):
        self.mailbox, self.path = mailbox, path
        self.template = video_stream
        self.has_audio = has_audio
        self.origin = media_time(first_packet.dts, first_packet.time_base)
        if self.origin is None:
            raise ValueError('Video timing is unavailable')
        self.queue = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.queued_bytes = 0
        self.stop = threading.Event()
        self.error_class = None
        self.finished = False
        self.thread = threading.Thread(target=self.run, daemon=True, name='arenti-local-recording')
        self.offer_video(first_packet)

    def offer(self, item, size):
        with self.lock:
            if self.stop.is_set():
                return
            if self.queued_bytes + size > MAX_QUEUE_BYTES or self.queue.full():
                self.error_class = 'BufferError'
                self.stop.set()
                return
            self.queued_bytes += size
            self.queue.put_nowait((item, size))

    def offer_video(self, packet):
        if not self.stop.is_set() and packet.size and packet.pts is not None and packet.dts is not None:
            data = bytes(packet)
            self.offer(('video', data, packet.pts, packet.dts,
                        Fraction(packet.time_base), packet.is_keyframe, packet.duration), len(data))

    def offer_audio(self, frame):
        timestamp = media_time(frame.pts, frame.time_base)
        if not self.stop.is_set() and self.has_audio and timestamp is not None:
            data = bytes(frame.planes[0])[:frame.samples * 2]
            self.offer(('audio', data, frame.samples, timestamp), len(data))

    def request_stop(self):
        with self.lock:
            if not self.finished:
                self.mailbox.status(recordingState='saving')
                self.stop.set()

    def alive(self):
        return self.thread.is_alive()

    def run(self):
        import av
        output = handle = None
        video_packets = audio_samples = 0
        try:
            if shutil.disk_usage(self.path.parent).free < MIN_FREE_BYTES:
                raise OSError('Not enough free disk space')
            handle = self.path.open('xb')
            output = av.open(handle, mode='w', format='mp4')
            video = output.add_stream_from_template(self.template)
            video.metadata.clear()
            audio = output.add_stream('aac', rate=48000) if self.has_audio else None
            if audio:
                audio.layout = 'mono'
                audio.bit_rate = 96000
            resampler = av.AudioResampler(format='fltp', layout='mono', rate=48000) if audio else None
            next_audio_pts = None
            last_disk_check = time.monotonic()
            self.mailbox.status(recordingState='recording', recordingStartedAt=time.monotonic(),
                                mediaSaved=True)
            while not self.stop.is_set() or not self.queue.empty():
                try:
                    item, size = self.queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                with self.lock:
                    self.queued_bytes -= size
                if item[0] == 'video':
                    _, data, pts, dts, base, keyframe, duration = item
                    packet = av.Packet(data)
                    packet.pts = pts - round(self.origin / base)
                    packet.dts = dts - round(self.origin / base)
                    packet.time_base = base
                    packet.duration = duration
                    packet.is_keyframe = keyframe
                    packet.stream = video
                    output.mux(packet)
                    video_packets += 1
                elif audio:
                    _, data, samples, timestamp = item
                    pts = round((timestamp - self.origin) * 48000)
                    trim = max(0, -pts, (next_audio_pts - pts) if next_audio_pts is not None else 0)
                    if trim >= samples:
                        continue
                    data, samples, pts = data[trim * 2:], samples - trim, pts + trim
                    frame = av.AudioFrame(format='s16', layout='mono', samples=samples)
                    frame.planes[0].update(data)
                    frame.sample_rate, frame.pts, frame.time_base = 48000, pts, Fraction(1, 48000)
                    for converted in resampler.resample(frame):
                        for packet in audio.encode(converted):
                            output.mux(packet)
                    next_audio_pts = pts + samples
                    audio_samples += samples
                if time.monotonic() - last_disk_check >= 1:
                    if shutil.disk_usage(self.path.parent).free < MIN_FREE_BYTES:
                        raise OSError('Disk space exhausted')
                    last_disk_check = time.monotonic()
            if audio:
                for converted in resampler.resample(None):
                    for packet in audio.encode(converted):
                        output.mux(packet)
                for packet in audio.encode(None):
                    output.mux(packet)
        except Exception as exc:
            self.error_class = type(exc).__name__
        finally:
            try:
                if output is not None:
                    output.close()
            except Exception as exc:
                self.error_class = type(exc).__name__
            finally:
                if handle is not None:
                    try:
                        handle.close()
                    except Exception as exc:
                        self.error_class = type(exc).__name__
            with self.lock:
                self.stop.set()
                self.finished = True
                self.mailbox.status(recordingState='error' if self.error_class else 'saved',
                                    recordingErrorClass=self.error_class, recordedVideoPackets=video_packets,
                                    recordedAudioSamples=audio_samples, recordingUpdatedAt=time.monotonic())


class CaptureController:
    def __init__(self, mailbox):
        self.mailbox = mailbox
        self.lock = threading.Lock()
        self.recording = None
        self.record_path = None
        self.snapshot_path = None
        self.snapshot_thread = None

    def start_recording(self, path):
        path = local_capture_path(path, '.mp4')
        with self.lock:
            if self.record_path or (self.recording and self.recording.alive()):
                raise RuntimeError('Recording is already active or saving')
            self.recording = None
            self.record_path = path
            self.mailbox.status(recordingState='waiting', recordingPath=str(path), recordingErrorClass=None,
                                recordingUpdatedAt=time.monotonic())

    def stop_recording(self):
        with self.lock:
            if self.record_path:
                self.record_path = None
                self.mailbox.status(recordingState='cancelled', recordingUpdatedAt=time.monotonic())
            if self.recording and self.recording.alive():
                self.recording.request_stop()

    def request_snapshot(self, path):
        path = local_capture_path(path, '.png')
        with self.lock:
            if self.snapshot_path or (self.snapshot_thread and self.snapshot_thread.is_alive()):
                raise RuntimeError('A screenshot is already pending')
            self.snapshot_path = path
            self.mailbox.status(snapshotState='waiting', snapshotPath=str(path), snapshotErrorClass=None,
                                snapshotUpdatedAt=time.monotonic())

    def video_packet(self, packet, video_stream, has_audio):
        with self.lock:
            if self.record_path and packet.is_keyframe and packet.size and packet.dts is not None:
                try:
                    self.recording = RecordingSession(self.mailbox, self.record_path, video_stream,
                                                       packet, has_audio)
                    self.record_path = None
                    self.recording.thread.start()
                except Exception as exc:
                    self.record_path = None
                    self.recording = None
                    self.mailbox.status(recordingState='error', recordingErrorClass=type(exc).__name__,
                                        recordingUpdatedAt=time.monotonic())
                return
            recording = self.recording
        if recording:
            try:
                recording.offer_video(packet)
            except Exception as exc:
                recording.error_class = type(exc).__name__
                recording.request_stop()

    def audio_frame(self, frame):
        with self.lock:
            recording = self.recording
        if recording:
            try:
                recording.offer_audio(frame)
            except Exception as exc:
                recording.error_class = type(exc).__name__
                recording.request_stop()

    def video_frame(self, frame):
        with self.lock:
            if not self.snapshot_path:
                return
            path, self.snapshot_path = self.snapshot_path, None
            def save():
                try:
                    save_png(frame, path)
                    self.mailbox.status(snapshotState='saved', mediaSaved=True, snapshotUpdatedAt=time.monotonic())
                except Exception as exc:
                    self.mailbox.status(snapshotState='error', snapshotErrorClass=type(exc).__name__,
                                        snapshotUpdatedAt=time.monotonic())
            self.mailbox.status(snapshotState='saving')
            self.snapshot_thread = threading.Thread(target=save, daemon=True, name='arenti-local-screenshot')
            try:
                self.snapshot_thread.start()
            except Exception as exc:
                self.snapshot_thread = None
                self.mailbox.status(snapshotState='error', snapshotErrorClass=type(exc).__name__,
                                    snapshotUpdatedAt=time.monotonic())

    def stream_ended(self):
        self.stop_recording()
        with self.lock:
            if self.snapshot_path:
                self.snapshot_path = None
                self.mailbox.status(snapshotState='cancelled', snapshotUpdatedAt=time.monotonic())

    def close(self):
        self.stream_ended()
        if self.recording:
            self.recording.thread.join()
        if self.snapshot_thread:
            self.snapshot_thread.join()
