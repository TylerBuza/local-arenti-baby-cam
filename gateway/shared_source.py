"""Bounded packet fan-out: one read-only RTSP input per quality, private muxers.

No shared decoder state, media files, packet dropping inside a view, or network
work at construction. A late join starts a fresh MP4 at its next video keyframe.
"""
from dataclasses import dataclass
import queue
import threading
import time

from gateway.media import open_camera_source, StreamBacklogError

MAX_PACKET_BYTES = 2 * 1024 * 1024
MAX_PACKET_COUNT = 512
MAX_PACKET_AGE = 2.0


@dataclass(frozen=True)
class PacketData:
    data: bytes
    pts: object
    dts: object
    duration: int
    time_base: object
    index: int
    keyframe: bool
    received_at: float


class Subscription:
    def __init__(self, hub, stop):
        self.hub, self.stop = hub, stop
        self.queue = queue.Queue(MAX_PACKET_COUNT)
        self.lock = threading.Lock()
        self.bytes_queued = 0
        self.closed = False
        self.error = None
        self.last_read_at = None
        self.decoders = {}

    def offer(self, packet):
        with self.lock:
            if self.closed or self.stop.is_set() or self.error:
                return
            if self.bytes_queued + len(packet.data) > MAX_PACKET_BYTES or self.queue.full():
                self.error = StreamBacklogError
                return
            self.queue.put_nowait(packet)
            self.bytes_queued += len(packet.data)

    def __enter__(self):
        try:
            if not self.hub.ready.wait(7) or self.stop.is_set() or self.hub.streams is None:
                raise RuntimeError('Shared input unavailable')
            import av
            self.streams = self.hub.streams
            # Each viewer owns its audio codec contexts. Input Stream objects are
            # metadata only: never call packet.decode() on a shared context.
            for stream in self.streams.audio:
                original = stream.codec_context
                decoder = av.CodecContext.create(original.name, 'r')
                if original.extradata:
                    decoder.extradata = bytes(original.extradata)
                decoder.sample_rate = original.sample_rate
                decoder.layout = original.layout.name
                self.decoders[stream.index] = decoder
            return self
        except Exception:
            self.close()
            raise

    def decode_audio(self, packet):
        return self.decoders[packet.stream.index].decode(packet)

    def demux(self):
        import av
        while not self.stop.is_set():
            with self.lock:
                if self.error:
                    raise self.error('Shared input consumer exceeded bounds')
                if self.closed:
                    return
            try:
                item = self.queue.get(timeout=0.2)
            except queue.Empty:
                if self.hub.finished.is_set():
                    if self.hub.error:
                        raise RuntimeError('Shared input ended')
                    return
                continue
            with self.lock:
                self.bytes_queued -= len(item.data)
            if time.monotonic() - item.received_at > MAX_PACKET_AGE:
                raise StreamBacklogError('Shared packet queue exceeded age limit')
            packet = av.Packet(item.data)
            packet.pts, packet.dts, packet.duration = item.pts, item.dts, item.duration
            packet.time_base = item.time_base
            packet.stream = self.streams[item.index]
            packet.is_keyframe = item.keyframe
            self.last_read_at = item.received_at
            yield packet

    def close(self):
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.decoders.clear()
            while not self.queue.empty():
                self.queue.get_nowait()
            self.bytes_queued = 0
        self.hub.unsubscribe(self)

    def __exit__(self, *args):
        self.close()


class SourceHub:
    def __init__(self, quality, factory):
        self.quality, self.factory = quality, factory
        self.lock = threading.Condition(threading.RLock())
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.finished = threading.Event()
        self.streams = None
        self.error = False
        self.subscribers = set()
        self.thread = threading.Thread(target=self.run, name='phone-shared-' + quality, daemon=True)

    def subscribe(self, stop):
        with self.lock:
            if self.stop.is_set() or self.finished.is_set():
                raise BlockingIOError('Shared input is closing')
            view = Subscription(self, stop)
            self.subscribers.add(view)
            if self.thread.ident is None:
                self.thread.start()
            return view

    def unsubscribe(self, view):
        with self.lock:
            self.subscribers.discard(view)
            if not self.subscribers:
                self.stop.set()
            self.lock.notify_all()

    def run(self):
        try:
            with self.factory(self.quality) as source:
                with self.lock:
                    self.streams = source.streams
                    self.ready.set()
                try:
                    for packet in source.demux():
                        if self.stop.is_set():
                            break
                        if not packet.size or packet.stream.type not in ('video', 'audio') or packet.time_base is None:
                            continue
                        data = PacketData(bytes(packet), packet.pts, packet.dts, packet.duration,
                                          packet.time_base, packet.stream.index, packet.is_keyframe, time.monotonic())
                        if len(data.data) > MAX_PACKET_BYTES:
                            raise BufferError('Input packet exceeds bound')
                        with self.lock:
                            for subscriber in self.subscribers:
                                subscriber.offer(data)
                except Exception:
                    self.error = True
                finally:
                    self.finished.set()
                    # Keep input metadata alive until every muxer has released its
                    # subscription; do not close an AVStream underneath a viewer.
                    with self.lock:
                        self.lock.wait_for(lambda: not self.subscribers)
        except Exception:
            self.error = True
        finally:
            self.ready.set()
            self.finished.set()


class SharedSources:
    def __init__(self, factory=open_camera_source):
        self.factory = factory
        self.lock = threading.Lock()
        self.hubs = {}
        self.closed = False

    def subscribe(self, quality, stop):
        if quality not in ('high', 'low'):
            raise ValueError('Unknown quality')
        with self.lock:
            if self.closed or stop.is_set():
                raise PermissionError('Input pool stopped')
            hub = self.hubs.get(quality)
            if hub is not None and (hub.stop.is_set() or hub.finished.is_set()):
                if hub.thread.is_alive():
                    raise BlockingIOError('Prior input is still closing')
                hub = None
            if hub is None:
                hub = SourceHub(quality, self.factory)
                self.hubs[quality] = hub
            return hub.subscribe(stop)

    def close(self):
        with self.lock:
            self.closed = True
            hubs = list(self.hubs.values())
            for hub in hubs:
                hub.stop.set()
                with hub.lock:
                    for view in hub.subscribers:
                        view.stop.set()
        deadline = time.monotonic() + 6
        for hub in hubs:
            hub.thread.join(timeout=max(0, deadline - time.monotonic()))


class MovementLease:
    """One phone owns active movement; another phone's Stop cannot stop its hold."""
    def __init__(self):
        self.lock = threading.RLock()
        self.owner = None

    def command(self, view, action, apply):
        with self.lock:
            if self.owner is not None and (not self.owner.ptz or not self.owner.ptz.moving):
                self.owner = None
            if action == 'stop' and self.owner is not view:
                return {'accepted': True, 'moving': False, 'cloudRequests': 0}
            if self.owner is not None and self.owner is not view:
                return {'accepted': False, 'reason': 'busy', 'cloudRequests': 0}
            if action != 'stop':
                self.owner = view
            try:
                return apply()
            finally:
                if not view.ptz or not view.ptz.moving:
                    self.owner = None

    def close(self, view, apply):
        with self.lock:
            try:
                apply()
            finally:
                if self.owner is view and (not view.ptz or not view.ptz.moving):
                    self.owner = None
