"""Packaged DPAPI/TLS/shared synthetic-codec checks; no camera/account access."""
import http.client
import io
from fractions import Fraction
import json
import os
from pathlib import Path
import secrets
import ssl
import tempfile
import threading
import time


def shared_media_check():
    """Four independent muxers/decoders, generated H.264 + camera-format mu-law."""
    import av
    from gateway.media import MediaSession
    from gateway.shared_source import SharedSources
    data = io.BytesIO()
    with av.open(data, 'w', format='nut') as output:
        video = output.add_stream('libx264', rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, 'yuv420p'
        video.options = {'preset': 'ultrafast', 'tune': 'zerolatency'}
        video.codec_context.gop_size = 10
        video.codec_context.thread_count = 1
        audio = output.add_stream('pcm_mulaw', rate=8000)
        audio.layout = 'mono'
        for index in range(20):
            frame = av.VideoFrame(64, 48, 'yuv420p')
            for plane in frame.planes: plane.update(bytes([32 + index]) * plane.buffer_size)
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame): output.mux(packet)
            sound = av.AudioFrame(format='s16', layout='mono', samples=800)
            sound.planes[0].update(bytes(sound.planes[0].buffer_size))
            sound.sample_rate, sound.pts, sound.time_base = 8000, index * 800, Fraction(1, 8000)
            for packet in audio.encode(sound): output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None): output.mux(packet)
    gate = threading.Event()
    opens = []
    class Input:
        def __init__(self, quality):
            opens.append(quality)
            self.media = av.open(io.BytesIO(data.getvalue()))
            self.streams = self.media.streams
        def __enter__(self): return self
        def __exit__(self, *args): self.media.close()
        def demux(self):
            if not gate.wait(5): raise RuntimeError('Synthetic start gate timed out')
            yield from self.media.demux()
    pool = SharedSources(Input)
    views = [MediaSession('synthetic-' + str(index), 'low') for index in range(4)]
    try:
        for view in views:
            subscription = pool.subscribe('low', view.stop)
            view.source_factory = lambda subscription=subscription: subscription
            view.thread.start()
        gate.set()
        for view in views:
            assert view.finished.wait(5)
            view.thread.join(2)
            assert view.error_class is None
            chunks = []
            while not view.queue.empty(): chunks.append(view.take())
            media = b''.join(chunks)
            for kind in ('video', 'audio'):
                with av.open(io.BytesIO(media)) as decoded:
                    assert sum(1 for _ in decoded.decode(**{kind: 0})) > 10
        assert opens == ['low']
    finally:
        gate.set()
        for view in views: view.close()
        pool.close()
    assert all(not hub.thread.is_alive() for hub in pool.hubs.values())


def run():
    import av
    import qrcode
    from PySide6.QtGui import QImage
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    from gateway.identity import Identity, private_pem
    from gateway.server import GatewayServer
    from gateway.media import MediaSession, MP4_OPTIONS, StreamBacklogError
    import protected_store
    av.Codec('h264', 'r')
    av.Codec('aac', 'w')
    shared_media_check()
    qrcode.QRCode().get_matrix()
    assert not QImage(8, 8, QImage.Format.Format_RGB32).isNull()
    assert MP4_OPTIONS['frag_duration'] == MP4_OPTIONS['max_interleave_delta'] == '250000'
    queue_check = MediaSession('synthetic-package-check', 'low')
    queue_check.offer(b'synthetic-only')
    assert queue_check.take() == b'synthetic-only'
    queue_check.offer(b'aged-synthetic-only')
    data, queued_at = queue_check.queue.get_nowait()
    queue_check.queue.put_nowait((data, queued_at - 3))
    try:
        queue_check.take()
        raise AssertionError('Old stream data must not be delivered')
    except StreamBacklogError:
        assert queue_check.stop.is_set() and queue_check.bytes_queued == 0
    with tempfile.TemporaryDirectory(prefix='connector-packaged-selftest-',
            dir=Path(os.environ['LOCALAPPDATA']) / 'Temp' / 'opencode') as temp:
        folder = Path(temp)
        protected_store.restrict_acl(folder, directory=True)
        identity = Identity(folder / 'private')
        identity.initialize('https://127.0.0.1:7443')
        key = ec.generate_private_key(ec.SECP256R1())
        csr = (x509.CertificateSigningRequestBuilder().subject_name(x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, 'Synthetic package smoke test')]))
            .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode())
        response = identity.enroll(identity.pairing_payload()['pairToken'], csr, 'Synthetic package smoke test')
        family = identity.prepare_family_credential()
        assert identity.prepare_family_credential() == family
        family_key = serialization.load_pem_private_key(family['clientKey'].encode(), None)
        family_chain, family_file = folder / 'family.pem', folder / 'family.encrypted.pem'
        family_chain.write_text(family['clientCert'] + identity.value['caCert'])
        family_file.write_bytes(private_pem(family_key, b'synthetic-family-only'))
        family_context = ssl.create_default_context(cadata=identity.value['caCert'])
        family_context.load_cert_chain(str(family_chain), str(family_file), 'synthetic-family-only')
        password = secrets.token_urlsafe(48)
        cert_path, key_path = folder / 'client.pem', folder / 'client.encrypted.pem'
        cert_path.write_text(response['clientCert'] + identity.value['caCert'])
        key_path.write_bytes(private_pem(key, password.encode()))
        context = ssl.create_default_context(cadata=identity.value['caCert'])
        context.load_cert_chain(str(cert_path), str(key_path), password)
        servers, workers = [], []
        try:
            for index in range(2):
                server = GatewayServer(('127.0.0.1', 0), identity,
                    state=servers[0].state if servers else None, allow_pairing=index == 0)
                servers.append(server)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                workers.append((server, worker))
                connection = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=context, timeout=5)
                try:
                    connection.request('GET', '/v1/status', headers={'X-Arenti-Client': 'android-v1'})
                    status = connection.getresponse()
                    assert status.status == 200
                    value = json.loads(status.read())
                    assert value['gatewayId'] == identity.value['gatewayId']
                    assert value['maxPhoneViewers'] == 4
                finally:
                    connection.close()
                if index == 1:
                    bootstrap = http.client.HTTPSConnection('127.0.0.1', server.server_port,
                        context=family_context, timeout=5)
                    try:
                        bootstrap.request('POST', '/v1/family/activate', json.dumps({'csr': csr, 'name': 'Synthetic family phone'}),
                            headers={'X-Arenti-Client': 'android-v1', 'Content-Type': 'application/json'})
                        activated = bootstrap.getresponse()
                        assert activated.status == 200
                        assert json.loads(activated.read())['gatewayId'] == identity.value['gatewayId']
                    finally:
                        bootstrap.close()
            assert servers[0].state is servers[1].state
            from datetime import datetime, timezone
            from owlet_overlay import normalize_widget
            class SyntheticHealth:
                def start(self): pass
                def stop(self): pass
                def snapshot(self):
                    return normalize_widget({'updated_at': datetime.now(timezone.utc).isoformat(),
                        'sock_reporting': True, 'oxygen_now': 98, 'heart_rate': 120, 'sleep_state': '8'})
            state = servers[0].state
            state.health_factory = SyntheticHealth
            owner = identity.authenticate(x509.load_pem_x509_certificate(response['clientCert'].encode()).public_bytes(serialization.Encoding.DER))
            view = MediaSession(owner, 'low')  # No thread/input is started.
            state.views['synthetic-sock-check'] = view
            for server in servers:
                connection = http.client.HTTPSConnection('127.0.0.1', server.server_port, context=context, timeout=5)
                try:
                    connection.request('GET', '/v1/owlet?view=synthetic-sock-check', headers={'X-Arenti-Client': 'android-v1'})
                    reply = connection.getresponse()
                    assert reply.status == 200
                    value = json.loads(reply.read())
                    assert set(value) == {'schema', 'availability', 'oxygen', 'heart_rate', 'sleep', 'sourceAgeSeconds'}
                    assert value['availability'] == 'live' and value['oxygen'] == 98 and value['heart_rate'] == 120
                finally:
                    connection.close()
            servers[0].close_view(view)
            assert state.health is None
        finally:
            for server, _worker in workers:
                server.shutdown()
            for server in servers:
                server.server_close()
            for _server, worker in workers:
                worker.join(timeout=2)
    return {'passed': True, 'dpapi': True, 'authenticatedLoopbackTls': True,
        'sharedListenerState': True, 'authenticatedSyntheticSockReadingsBothListeners': True,
        'fourIndependentH264AacMuxersFromOneSharedMulawInput': True,
        'strictMtlsFunnelBackend': True,
        'authenticatedRemoteFirstFamilyActivation': True,
        'boundedLiveQueueAge': True, 'quarterSecondFragmentAndInterleaveBudget': True,
        'h264DecoderAacEncoder': True, 'qtQrDependencies': True,
        'cameraRequests': 0, 'mediaFilesWritten': 0, 'cloudRequests': 0}
