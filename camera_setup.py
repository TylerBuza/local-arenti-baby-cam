"""First-run configuration of an already enabled local ONVIF/RTSP camera."""
from urllib.parse import urlsplit
import threading

import protected_store
from lan_tools import discover, setup_host, device_identity
from onvif_local import OnvifClient, anonymous_rtsp_status, validate_stream, local_url


def configured():
    try:
        value = protected_store.load()
        return value.get('state') == 'verified' and bool(value.get('cameraSha256'))
    except Exception:
        return False


def verify_and_save(url, username, password):
    if not 1 <= len(username) <= 128 or not 1 <= len(password) <= 128:
        raise ValueError('Enter the local ONVIF username and password')
    with setup_host(urlsplit(url).hostname):
        local_url(url)
        client = OnvifClient(username, password)
        try:
            identity = device_identity(client, url)
            uri, _profiles = client.stream_uri(url)
            if anonymous_rtsp_status(uri) not in (401, 403):
                raise ValueError('Camera must reject anonymous RTSP access')
            result = validate_stream(uri, username, password, seconds=2)
            if result['videoFrames'] < 15:
                raise ValueError('Local video could not be verified')
            value = {'schema':1, 'state':'verified', 'deviceUrl':url, 'rtspUri':uri,
                'username':username, 'password':password, 'cameraSha256':identity}
            protected_store.save(protected_store.ACTIVE, value)
        finally:
            client.close()


def show_setup(parent=None):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QComboBox, QLineEdit, QPushButton

    class Events(QObject):
        found = Signal(list)
        saved = Signal()
        failed = Signal(str)

    busy = [False]
    class SetupDialog(QDialog):
        def reject(self):
            if not busy[0]:
                super().reject()
        def closeEvent(self, event):
            if busy[0]:
                event.ignore()
            else:
                super().closeEvent(event)
    dialog = SetupDialog(parent)
    dialog.setWindowTitle('Set up your local camera')
    dialog.setMinimumWidth(500)
    layout = QVBoxLayout(dialog)
    explanation = QLabel('Arenti/D3 beta · Enable password-protected ONVIF/RTSP on your camera first.\n'
        'Use its local camera credentials, not your cloud account.\n'
        'Discovery stays on your LAN. Testing reads live video briefly; nothing is recorded.')
    explanation.setWordWrap(True)
    layout.addWidget(explanation)
    search = QPushButton('Find cameras on this network')
    layout.addWidget(search)
    layout.addWidget(QLabel('ONVIF device-service URL (choose a result or enter it manually)'))
    address = QComboBox()
    address.setEditable(True)
    address.lineEdit().setPlaceholderText('http://CAMERA-LAN-IP:8000/onvif/device_service')
    layout.addWidget(address)
    layout.addWidget(QLabel('Local username'))
    username = QLineEdit('admin')
    username.setMaxLength(128)
    layout.addWidget(username)
    layout.addWidget(QLabel('Local password'))
    password = QLineEdit()
    password.setEchoMode(QLineEdit.EchoMode.Password)
    password.setMaxLength(128)
    layout.addWidget(password)
    status = QLabel('After setup, reserve the camera IP in your router for reliable reconnects.')
    status.setWordWrap(True)
    layout.addWidget(status)
    save = QPushButton('Test local video and save')
    layout.addWidget(save)
    events = Events(dialog)
    def enable(value):
        busy[0] = not value
        for widget in (search, address, username, password, save):
            widget.setEnabled(value)
    def found(values):
        enable(True)
        address.clear()
        address.addItems(values)
        status.setText(f'Found {len(values)} candidate service(s). Select your camera.' if values else
            'No discovery reply. Check the same LAN, ONVIF setting and Wi-Fi client isolation; or enter the camera URL.')
    def failed(kind):
        enable(True)
        status.setText('Setup did not complete (' + kind + '). Check ONVIF/RTSP, local credentials and camera address. No cloud fallback.')
    def launch(operation):
        enable(False)
        def run():
            try:
                operation()
            except Exception as error:
                events.failed.emit(type(error).__name__)  # Never expose URLs/passwords from exception text.
        threading.Thread(target=run, daemon=True).start()
    def scan():
        status.setText('Discovering on local interfaces…')
        launch(lambda: events.found.emit(discover()))
    def commit():
        values = address.currentText().strip(), username.text(), password.text()
        status.setText('Checking camera identity, password protection and live video…')
        def work():
            verify_and_save(*values)
            events.saved.emit()
        launch(work)
    def saved():
        enable(True)
        password.clear()
        dialog.accept()
    events.found.connect(found)
    events.failed.connect(failed)
    events.saved.connect(saved)
    search.clicked.connect(scan)
    save.clicked.connect(commit)
    return dialog.exec() == QDialog.DialogCode.Accepted
