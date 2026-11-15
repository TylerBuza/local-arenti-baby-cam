"""Always-on-top native video surface host; never opens a camera or copies media."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel


class PopoutWindow(QWidget):
    def __init__(self, owner, make_button, make_icon):
        # Independent top-level tool window: minimizing the main viewer must not
        # minimize the overlay. The owner explicitly docks/closes it on tray/Exit.
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTitleHint | Qt.WindowType.WindowCloseButtonHint)
        self.owner = owner
        self.make_icon = make_icon
        self.setObjectName('root')
        self.setWindowTitle('Local Arenti Baby Cam — Pop-out')
        self.setWindowIcon(owner.windowIcon())
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumSize(320, 210)
        self.resize(480, 340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        self.video_layout = QVBoxLayout()
        self.video_layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.video_layout, 1)
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.play = make_button('  Start', 'play')
        self.play.setAccessibleName('Start or stop pop-out preview')
        self.play.clicked.connect(self.toggle_play)
        self.mute = make_button('  Muted', 'muted')
        self.mute.setAccessibleName('Toggle pop-out camera audio')
        self.mute.clicked.connect(owner.toggle_mute)
        self.dock = make_button('  Return', 'expand')
        self.dock.setToolTip('Return video to the main viewer · Escape')
        self.dock.setAccessibleName('Return video to main viewer')
        self.dock.clicked.connect(lambda: owner.dock_video(restore_main=True))
        for button in (self.play, self.mute, self.dock):
            controls.addWidget(button, 1)
        layout.addLayout(controls)
        self.status = QLabel('Preview stopped', objectName='small')
        self.status.setContentsMargins(4, 0, 4, 2)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.sync_controls()

    def toggle_play(self):
        if self.owner.camera or self.owner.preview_recovery.pending:
            self.owner.stop_live()
        else:
            self.owner.start_live()
        self.sync_controls()

    def sync_controls(self):
        owner = self.owner
        retrying = owner.preview_recovery.pending
        active = bool(owner.camera)
        self.play.setText('  Cancel retry' if retrying else 'Stopping…' if owner.stopping else '  Stop' if active else '  Start')
        self.play.setIcon(self.make_icon('stop' if active or retrying else 'play'))
        self.play.setEnabled((not owner.stopping or retrying) and not owner.closing and not owner.exiting)
        self.mute.setText('  Muted' if owner.muted else '  Audio on')
        self.mute.setIcon(self.make_icon('muted' if owner.muted else 'volume'))
        self.mute.setEnabled(active and not owner.stopping)
        if retrying:
            state = owner.status.text()
        elif owner.stopping:
            state = 'Stopping preview…'
        elif not active:
            state = 'Preview stopped · press Start to connect'
        elif owner.mailbox and owner.mailbox.recent():
            state = 'Live · ' + ('muted' if owner.muted else 'audio on')
        else:
            state = 'Waiting for fresh camera video…'
        self.status.setText(state)

    def closeEvent(self, event):
        if self.owner.pip_window is self:
            self.owner.dock_video(close_window=False, restore_main=True)
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)
