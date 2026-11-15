"""Start/stop explicit LAN and existing Tailscale listeners together."""
import threading

from gateway.media import MediaSession
from gateway.routes import listener_addresses
from gateway.server import GatewayServer, GatewayState


class GatewayListeners:
    def __init__(self, local_url, remote_url, identity, media_factory=MediaSession, *, funnel=False):
        self.state = GatewayState(identity)
        self.servers, self.threads = [], []
        try:
            for index, address in enumerate(listener_addresses(local_url, remote_url, funnel=funnel)):
                self.servers.append(GatewayServer(address, identity,
                    media_factory=media_factory, state=self.state,
                    allow_pairing=not (funnel and index > 0)))
        except Exception:
            for server in self.servers:
                server.server_close()
            raise

    def start(self):
        if self.state.closing.is_set() or self.threads:
            raise RuntimeError('Listener group already started or stopped')
        try:
            for server in self.servers:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                self.threads.append((server, thread))
        except Exception:
            self.close()
            raise

    def close(self):
        self.state.closing.set()
        for server, _thread in self.threads:
            server.shutdown()
        for server in self.servers:
            server.server_close()
        for _server, thread in self.threads:
            thread.join(timeout=2)
        self.threads.clear()
