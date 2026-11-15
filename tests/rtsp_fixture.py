"""Synthetic H264 RTSP/TCP camera, loopback only. Never contacts a real camera.

Run with the project Python environment. Fixed fixture-only credentials are not
camera credentials. First PLAY disconnects after four seconds to test recovery.
"""
import base64
import re
import socketserver
import struct
import threading
import time
import av

USER = "fixture-viewer"
PASSWORD = "fixture:@% password"


def frames():
    codec = av.CodecContext.create("libx264", "w")
    codec.width, codec.height, codec.pix_fmt = 320, 180, "yuv420p"
    from fractions import Fraction
    codec.time_base, codec.framerate = Fraction(1, 10), Fraction(10, 1)
    codec.options = {"preset": "ultrafast", "tune": "zerolatency", "x264-params": "keyint=10:repeat-headers=1"}
    result = []
    for index in range(30):
        frame = av.VideoFrame(320, 180, "yuv420p")
        for i, plane in enumerate(frame.planes):
            plane.update(bytes([40 + index * 4 if i == 0 else 128]) * plane.buffer_size)
        frame.pts = index
        for packet in codec.encode(frame):
            result.append([n for n in re.split(b"\x00\x00\x00?\x01", bytes(packet)) if n])
    return result


class Camera(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.frames = frames()
        self.plays = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 18554), Client)


class Client(socketserver.StreamRequestHandler):
    def handle(self):
        self.write_lock = threading.Lock()
        self.finished = threading.Event()
        try:
            while not self.finished.is_set():
                line = self.rfile.readline(8192).decode("ascii").strip()
                if not line:
                    break
                method, uri, _ = line.split(" ", 2)
                headers = {}
                for _ in range(64):
                    line = self.rfile.readline(8192).decode("ascii").strip()
                    if not line:
                        break
                    key, value = line.split(":", 1)
                    headers[key.lower()] = value.strip()
                cseq = headers.get("cseq", "1")
                auth = "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
                if method != "OPTIONS" and headers.get("authorization") != auth and "/anonymous" not in uri:
                    self.reply(cseq, "401 Unauthorized", {"WWW-Authenticate": 'Basic realm="Synthetic camera"'})
                    continue
                if method == "OPTIONS":
                    self.reply(cseq, extra={"Public": "OPTIONS, DESCRIBE, SETUP, PLAY, TEARDOWN"})
                elif method == "DESCRIBE":
                    nals = self.server.frames[0]
                    sps = next(n for n in nals if n[0] & 31 == 7)
                    pps = next(n for n in nals if n[0] & 31 == 8)
                    body = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=Synthetic camera\r\nt=0 0\r\n"
                            "a=control:*\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
                            f"a=fmtp:96 packetization-mode=1;sprop-parameter-sets={base64.b64encode(sps).decode()},{base64.b64encode(pps).decode()}\r\n"
                            "a=control:track1\r\nm=audio 0 RTP/AVP 0\r\na=rtpmap:0 PCMU/8000\r\na=control:track2\r\n").encode()
                    self.reply(cseq, extra={"Content-Type": "application/sdp", "Content-Base": uri + "/"}, body=body)
                elif method == "SETUP":
                    assert "TCP" in headers["transport"]
                    self.reply(cseq, extra={"Session": "fixture;timeout=30", "Transport": headers["transport"]})
                elif method == "PLAY":
                    self.reply(cseq, extra={"Session": "fixture", "Range": "npt=0.000-", "RTP-Info": f"url={uri}/track1;seq=0;rtptime=0"})
                    with self.server.lock:
                        self.server.plays += 1
                        disconnect = self.server.plays == 1
                    threading.Thread(target=self.stream, args=(disconnect,), daemon=True).start()
                elif method == "TEARDOWN":
                    self.reply(cseq)
                    break
        except (OSError, ValueError):
            pass
        finally:
            self.finished.set()

    def reply(self, cseq, status="200 OK", extra=None, body=b""):
        headers = {"CSeq": cseq, **(extra or {}), "Content-Length": str(len(body))}
        message = f"RTSP/1.0 {status}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n"
        with self.write_lock:
            self.request.sendall(message.encode() + body)

    def stream(self, disconnect):
        sequence = 0
        try:
            for index in range(6000):
                if self.finished.is_set():
                    return
                if disconnect and index == 40:
                    self.request.shutdown(2)
                    return
                nals = self.server.frames[index % len(self.server.frames)]
                for position, nal in enumerate(nals):
                    packet = struct.pack("!BBHII", 128, 96 | (128 if position == len(nals) - 1 else 0), sequence % 65536, index * 9000, 42) + nal
                    with self.write_lock:
                        self.request.sendall(b"$\x00" + struct.pack("!H", len(packet)) + packet)
                    sequence += 1
                # Synthetic G.711 mu-law silence, a common camera audio format.
                audio = struct.pack("!BBHII", 128, 0, index % 65536, index * 800, 43) + b"\xff" * 800
                with self.write_lock:
                    self.request.sendall(b"$\x02" + struct.pack("!H", len(audio)) + audio)
                if self.finished.wait(.1):
                    return
        except OSError:
            pass


if __name__ == "__main__":
    with Camera() as server:
        threading.Timer(600, server.shutdown).start()
        server.serve_forever()
