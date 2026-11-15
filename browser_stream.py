"""Copy the live MPEG-TS stream into in-memory fragmented MP4 for browsers."""
import io
import queue


def mp4_codecs(data):
    index = data.find(b'avcC')
    if index < 4 or index + 8 > len(data) or data[index + 4] != 1:
        raise ValueError('H.264 browser initialization unavailable')
    video = 'avc1.' + data[index + 5:index + 8].hex()
    return video + (', mp4a.40.2' if b'mp4a' in data else '')


def moov_complete(data):
    offset = 0
    while offset + 8 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], 'big')
        kind = data[offset + 4:offset + 8]
        if size < 8:
            raise ValueError('Invalid MP4 initialization')
        if offset + size > len(data):
            return False
        if kind == b'moov':
            return True
        offset += size
    return False


class QueueReader(io.RawIOBase):
    def __init__(self, owner, first):
        self.owner = owner
        self.buffer = bytearray(first)
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        return self.position

    def read(self, size=-1):
        size = 32768 if size < 0 else size
        if size == 0:
            return b''
        while not self.buffer:
            if self.owner.stop.is_set():
                return b''
            try:
                self.buffer.extend(self.owner.take(timeout=0 if self.owner.finished.is_set() else 0.5))
            except queue.Empty:
                if self.owner.finished.is_set():
                    return b''
                continue
        data = bytes(self.buffer[:size])
        del self.buffer[:size]
        self.position += len(data)
        return data


class FragmentSink(io.RawIOBase):
    def __init__(self, on_start, on_data):
        self.pending = bytearray()
        self.started = False
        self.position = 0
        self.on_start, self.on_data = on_start, on_data

    def writable(self):
        return True

    def seekable(self):
        return False

    def tell(self):
        return self.position

    def write(self, data):
        self.position += len(data)
        if not self.started:
            self.pending.extend(data)
            if len(self.pending) > 1024 * 1024:
                raise ValueError('MP4 initialization exceeded its bound')
            if moov_complete(self.pending):
                self.on_start(mp4_codecs(self.pending))
                self.started = True
                self.on_data(bytes(self.pending))
                self.pending.clear()
        else:
            self.on_data(data)
        return len(data)


def remux(owner, first, on_start, on_data):
    import av
    av.logging.set_level(av.logging.PANIC)
    reader = QueueReader(owner, first)
    sink = FragmentSink(on_start, on_data)
    with av.open(reader, mode='r', format='mpegts', options={
        'probesize': '32768', 'analyzeduration': '1000000',
    }) as source:
        if not source.streams.video or source.streams.video[0].codec_context.name != 'h264':
            raise ValueError('Browser playback requires H.264; use live.ts for other codecs')
        with av.open(sink, mode='w', format='mp4', options={
            'movflags': 'frag_keyframe+empty_moov+default_base_moof',
            'frag_duration': '500000', 'flush_packets': '1',
        }) as output:
            mapping = {stream.index: output.add_stream_from_template(stream)
                       for stream in source.streams if stream.type in ('video', 'audio')}
            filters = {stream.index: av.bitstream.BitStreamFilterContext('aac_adtstoasc', stream, mapping[stream.index])
                       for stream in source.streams if stream.type == 'audio'}
            for stream in mapping.values():
                stream.codec_context.codec_tag = 'mp4a' if stream.type == 'audio' else 'avc1'
                if stream.type == 'audio':
                    # MPEG-TS carries AAC setup in ADTS, not codec extradata.
                    # empty_moov must contain AudioSpecificConfig before any
                    # packets arrive; tolerant file decoders are not MSE tests.
                    if (stream.codec_context.name != 'aac' or stream.codec_context.sample_rate != 16000
                            or len(stream.codec_context.layout.channels) != 1):
                        raise ValueError('Browser audio requires the bridge mono 16 kHz AAC output')
                    stream.codec_context.extradata = b'\x14\x08'  # AAC-LC, 16 kHz, mono
            output.start_encoding()
            for packet in source.demux():
                if owner.stop.is_set():
                    break
                if packet.dts is None or packet.stream.index not in mapping:
                    continue
                index = packet.stream.index
                packets = filters[index].filter(packet) if index in filters else [packet]
                for converted in packets:
                    converted.stream = mapping[index]
                    output.mux(converted)
    return sink.started
