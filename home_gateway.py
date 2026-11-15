"""Explicitly started home connector for the Android app; no vendor bootstrap."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import threading

from gateway.identity import Identity, checked_url
from gateway.listeners import GatewayListeners
from gateway.routes import tailscale_ipv4, tailscale_dns_name


def connector_widget(parent=None, *, embedded=False):
    """Reusable phone-access panel; constructing it never starts media/listeners."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
    from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QLabel, QLineEdit,
        QListWidget, QMessageBox, QPushButton, QVBoxLayout, QWidget, QCheckBox, QFrame, QScrollArea, QLayout)
    import qrcode

    class Window(QWidget):
        def __init__(self):
            super().__init__(parent)
            self.server = None
            self.identity = Identity()
            self.setWindowTitle('Local Arenti Baby Cam — Phone access')
            self.resize(580, 460)
            container = QVBoxLayout(self)
            container.setContentsMargins(0, 0, 0, 0)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget(objectName='root')
            scroll.setWidget(content)
            container.addWidget(scroll)
            outer = QVBoxLayout(content)
            outer.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            outer.setSpacing(18)
            if embedded:
                outer.setContentsMargins(28, 28, 28, 22)
            outer.addWidget(QLabel('CONNECTED DEVICES', objectName='eyebrow'))
            outer.addWidget(QLabel('Phone access', objectName='title'))
            subtitle = QLabel('Manage your home connector and the phones that can view your camera.', objectName='muted')
            subtitle.setWordWrap(True)
            outer.addWidget(subtitle)
            connection = QFrame(objectName='panel')
            layout = QVBoxLayout(connection)
            layout.setContentsMargins(22, 20, 22, 20)
            layout.setSpacing(12)
            outer.addWidget(connection)
            layout.addWidget(QLabel('Connection settings', objectName='sectionTitle'))
            layout.addWidget(QLabel('Home network · LAN HTTPS address', objectName='small'))
            from lan_tools import connector_origin
            self.local = QLineEdit((self.identity.value or {}).get('localUrl', connector_origin()))
            layout.addWidget(self.local)
            layout.addWidget(QLabel('Away from home · remote HTTPS address', objectName='small'))
            vpn_ip = tailscale_ipv4()
            dns_name = tailscale_dns_name()
            funnel_mode = bool((self.identity.value or {}).get('funnelMode', False))
            default_remote = (f'https://{dns_name}:8443' if funnel_mode and dns_name else
                              (f'https://{vpn_ip}:7443' if vpn_ip else ''))
            self.remote = QLineEdit((self.identity.value or {}).get('remoteUrl', ''))
            layout.addWidget(self.remote)
            self.funnel = QCheckBox('Use Windows Funnel — phone does not need Tailscale')
            self.funnel.setChecked(funnel_mode)
            def change_mode(enabled):
                if enabled and dns_name:
                    self.remote.setText(f'https://{dns_name}:8443')
                elif not enabled and vpn_ip:
                    self.remote.setText(f'https://{vpn_ip}:7443')
            self.funnel.toggled.connect(change_mode)
            layout.addWidget(self.funnel)
            actions = QHBoxLayout()
            self.start = QPushButton('Start connector')
            self.start.setObjectName('primary')
            self.pair = QPushButton('Pair Android')
            self.pair.setEnabled(False)
            actions.addWidget(self.start)
            actions.addWidget(self.pair)
            layout.addLayout(actions)
            self.status = QLabel('Stopped.', objectName='muted')
            self.status.setWordWrap(True)
            layout.addWidget(self.status)
            devices = QFrame(objectName='panel')
            outer.addWidget(devices, 1)
            layout = QVBoxLayout(devices)
            layout.setContentsMargins(22, 20, 22, 20)
            layout.setSpacing(12)
            layout.addWidget(QLabel('Paired devices', objectName='sectionTitle'))
            self.clients = QListWidget()
            layout.addWidget(self.clients)
            family = self.identity.family_status()
            self.family_access = QCheckBox('Family APK access')
            self.family_access.hide()  # Public builds use individual QR pairing only.
            self.family_access.setChecked(family['enabled'])
            self.family_access.setEnabled(family['available'] and not family['revoked'])
            self.family_access.setToolTip('All copies share a family credential. Disable this to block family activations and existing family phones. Manual QR-paired phones are unchanged.')
            self.family_access.toggled.connect(self.toggle_family)
            layout.addWidget(self.family_access)
            revoke = QPushButton('Revoke selected phone')
            revoke.setObjectName('quiet')
            layout.addWidget(revoke)
            note = QLabel('Keep this PC running for phone access.', objectName='small')
            note.setWordWrap(True)
            outer.addWidget(note)
            self.start.clicked.connect(self.toggle)
            self.pair.clicked.connect(self.pair_phone)
            revoke.clicked.connect(self.revoke)
            self.refresh_clients()

        def refresh_clients(self):
            self.clients.clear()
            self.fingerprints = []
            for fingerprint, client in (self.identity.value or {}).get('clients', {}).items():
                if not client.get('revoked'):
                    self.clients.addItem(client['name'])
                    self.fingerprints.append(fingerprint)

        def toggle(self):
            if self.server:
                self.stop()
                return
            try:
                local = checked_url(self.local.text(), local=True)
                remote = checked_url(self.remote.text()) if self.remote.text() else ''
                funnel = self.funnel.isChecked()
                self.identity.initialize(local, remote, funnel=funnel)
                self.server = GatewayListeners(local, remote, self.identity, funnel=funnel)
                self.server.start()
                self.local.setEnabled(False)
                self.remote.setEnabled(False)
                self.funnel.setEnabled(False)
                self.pair.setEnabled(True)
                self.start.setText('Stop connector')
                self.status.setText('Phone access running. Remote forwarding must be enabled separately.'
                    if funnel else
                    'Phone access running on LAN and Tailscale.'
                    if len(self.server.servers) == 2 else
                    ('Phone access running. Remote route configured.'
                      if remote else 'Phone access running on LAN.'))
            except Exception as exc:
                if self.server:
                    self.server.close()
                self.server = None
                self.status.setText('Could not start: ' + type(exc).__name__)

        def stop(self):
            self.identity.close_pairing()
            if self.server:
                self.server.close()
                self.server = None
            self.pair.setEnabled(False)
            self.local.setEnabled(True)
            self.remote.setEnabled(True)
            self.funnel.setEnabled(True)
            self.start.setText('Start connector')
            self.status.setText('Stopped. Android streaming and movement have ended.')

        def pair_phone(self):
            try:
                payload = self.identity.pairing_payload()
                # QR contains a five-minute, one-use enrollment secret, never a
                # camera password. It is rendered in memory only, never logged.
                code = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
                code.add_data(json.dumps(payload, separators=(',', ':')))
                code.make(fit=True)
                matrix = code.get_matrix()
                scale = max(3, 600 // len(matrix))
                image = QImage(len(matrix) * scale, len(matrix) * scale, QImage.Format.Format_RGB32)
                image.fill(QColor('white'))
                painter = QPainter(image)
                for y, row in enumerate(matrix):
                    for x, dark in enumerate(row):
                        if dark:
                            painter.fillRect(x * scale, y * scale, scale, scale, QColor('black'))
                painter.end()
                dialog = QDialog(self)
                dialog.setWindowTitle('Pair your Android phone')
                box = QVBoxLayout(dialog)
                label = QLabel('In the Android app choose Pair, then scan. Code expires in five minutes.\n'
                               'Keep this code private. Closing this window cancels unused pairing.')
                label.setWordWrap(True)
                box.addWidget(label)
                qr = QLabel()
                qr.setPixmap(QPixmap.fromImage(image))
                qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
                box.addWidget(qr)
                dialog.show()
                if sys.platform == 'win32':
                    ctypes.windll.user32.SetWindowDisplayAffinity(int(dialog.winId()), 0x11)
                dialog.exec()
            except Exception as exc:
                QMessageBox.warning(self, 'Pairing unavailable', type(exc).__name__)
            finally:
                self.identity.close_pairing()
                self.refresh_clients()

        def revoke(self):
            index = self.clients.currentRow()
            if 0 <= index < len(self.fingerprints):
                self.identity.revoke(self.fingerprints[index])
                self.refresh_clients()
                family = self.identity.family_status()
                self.family_access.blockSignals(True)
                self.family_access.setChecked(family['enabled'])
                self.family_access.setEnabled(family['available'] and not family['revoked'])
                self.family_access.blockSignals(False)

        def toggle_family(self, enabled):
            try:
                self.identity.set_family_access(enabled)
                self.status.setText('Family APK access on.'
                    if enabled else 'Family APK access disabled, including previously activated family phones.')
            except Exception:
                self.family_access.blockSignals(True)
                self.family_access.setChecked(self.identity.family_status()['enabled'])
                self.family_access.blockSignals(False)
                self.status.setText('Family access change was not confirmed; no credential is displayed.')

        def closeEvent(self, event):
            if not embedded:
                self.stop()
            event.accept()

    return Window()


def gui(start=False):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = connector_widget()
    window.show()
    if start:
        QTimer.singleShot(0, window.toggle)
    return app.exec()


def main():
    if getattr(sys, 'frozen', False):
        import desktop_store
        desktop_store.configure()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true', help='Explicit headless start; otherwise show management UI')
    parser.add_argument('--start', action='store_true', help='Explicitly start the connector after showing its UI')
    parser.add_argument('--bind', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=7443)
    parser.add_argument('--remote-url', default='')
    parser.add_argument('--funnel', action='store_true', help='Separate strict-mTLS loopback backend for Windows Funnel')
    parser.add_argument('--self-test-report', type=Path, help='Synthetic packaged smoke test; report only, no camera')
    args = parser.parse_args()
    if args.self_test_report:
        try:
            from gateway.selftest import run
            result = run()
        except Exception as exc:
            result = {'passed': False, 'errorClass': type(exc).__name__}
        args.self_test_report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        return 0 if result['passed'] else 1
    if not args.serve:
        return gui(start=args.start)
    identity = Identity()
    local = checked_url(f'https://{args.bind}:{args.port}', local=True)
    identity.initialize(local, args.remote_url, funnel=args.funnel)
    server = GatewayListeners(local, args.remote_url, identity, funnel=args.funnel)
    server.start()
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Error classes only: no secret-bearing URLs/configuration/tracebacks.
        print(json.dumps({'errorClass': type(exc).__name__}))
        raise SystemExit(1)
