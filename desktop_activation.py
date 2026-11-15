"""Current-user-only local IPC: show the existing tray app, never start video."""
import hashlib
import os
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QTimer, QEventLoop


def server_name():
    scope = os.path.normcase(os.environ['LOCALAPPDATA']).encode()
    return 'LocalArentiBabyCam-' + hashlib.sha256(scope).hexdigest()[:20]


def show_existing():
    socket = QLocalSocket()
    loop, deadline = QEventLoop(), QTimer()
    deadline.setSingleShot(True)
    acknowledged = []
    def send():
        socket.write(b'show\n')
        socket.flush()
    def read():
        if bytes(socket.readAll()) == b'ok\n':
            acknowledged.append(True)
        loop.quit()
    socket.connected.connect(send)
    socket.readyRead.connect(read)
    socket.errorOccurred.connect(lambda _error: loop.quit())
    socket.disconnected.connect(loop.quit)
    deadline.timeout.connect(loop.quit)
    deadline.start(1000)
    socket.connectToServer(server_name())
    loop.exec()
    deadline.stop()
    socket.abort()
    return bool(acknowledged)


def listen_for_activation(window):
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    connections = set()

    def accept():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            if len(connections) >= 8:
                socket.abort()
                socket.deleteLater()
                continue
            connections.add(socket)
            socket.setReadBufferSize(64)
            buffer = bytearray()
            deadline = QTimer(socket)
            deadline.setSingleShot(True)
            deadline.timeout.connect(socket.abort)
            deadline.start(1500)
            def read(current=socket, data=buffer):
                data.extend(bytes(current.read(64 - len(data))))
                if b'\n' not in data and len(data) < 64:
                    return
                if data == b'show\n':
                    window.show_viewer()
                    current.write(b'ok\n')
                    current.flush()
                current.disconnectFromServer()
            def cleanup(current=socket):
                connections.discard(current)
                current.deleteLater()
            socket.readyRead.connect(read)
            socket.disconnected.connect(cleanup)
            if socket.bytesAvailable():
                read()
    server.newConnection.connect(accept)
    if not server.listen(server_name()):
        # Never remove/reset someone else's named pipe. The tray still works.
        server.deleteLater()
        return None
    return server
