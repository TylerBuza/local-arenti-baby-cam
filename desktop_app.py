"""Arenti Local Viewer: native Windows UI, local RTSP audio/video and bounded PTZ."""
import argparse
from datetime import datetime
import json
import logging
import math
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QLockFile, QStandardPaths, Qt, QTimer, QSize, QRectF, qInstallMessageHandler
from PySide6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPixmap, QLinearGradient, QPen
from PySide6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
    QSystemTrayIcon, QMenu, QStackedWidget, QCheckBox, QScrollArea, QLayout, QStyle, QStyleOptionSlider)

import desktop_store
import desktop_autostart
import desktop_activation
from desktop_input import physical_hold_down
from desktop_camera import CameraWorker, MediaMailbox, MovementWorker
from desktop_recovery import PreviewRecovery
from desktop_overlay import video_clock, owlet_freshness
from desktop_pip import PopoutWindow
import protected_store
from owlet_overlay import display_parts, empty_snapshot

VERSION = '0.1.0-beta'
ACCENT = '#67dfc5'
INK = '#edf4f8'
MUTED = '#9aafbf'
PATHS = {
    'camera': '<rect x="3" y="6" width="13" height="12" rx="3"/><path d="m16 10 5-3v10l-5-3"/>',
    'play': '<path d="m8 5 11 7-11 7Z"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    'record': '<circle cx="12" cy="12" r="7"/>',
    'up': '<path d="m6 14 6-6 6 6"/>',
    'down': '<path d="m6 10 6 6 6-6"/>',
    'left': '<path d="m14 6-6 6 6 6"/>',
    'right': '<path d="m10 6 6 6-6 6"/>',
    'volume': '<path d="M10 5 5 9H2v6h3l5 4Z"/><path d="M14 8a6 6 0 0 1 0 8m3-11a10 10 0 0 1 0 14"/>',
    'muted': '<path d="M10 5 5 9H2v6h3l5 4Z"/><path d="m16 9 6 6m0-6-6 6"/>',
    'expand': '<path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/>',
    'pip': '<rect x="3" y="4" width="18" height="16" rx="2"/><rect x="11" y="11" width="8" height="6" rx="1"/>',
    'shield': '<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6Z"/><path d="m8 12 3 3 5-6"/>',
    'info': '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
}


def icon(name, color=INK, size=24):
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</g></svg>'
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    return QIcon(pixmap)


def app_icon():
    image = QPixmap(256, 256)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor('#12333a'))
    painter.drawRoundedRect(8, 8, 240, 240, 60, 60)
    icon('camera', ACCENT, 160).paint(painter, 48, 48, 160, 160)
    painter.end()
    return QIcon(image)


STYLE = """
QMainWindow, QWidget#root { background: #0b121c; color: #edf4f8; }
QWidget { font-family: 'Segoe UI'; font-size: 10pt; color: #edf4f8; }
QLabel { background: transparent; }
QFrame#sidebar { background: #0e1925; border-right: 1px solid #20303e; }
QFrame#panel { background: #121f2d; border: 1px solid #263746; border-radius: 18px; }
QFrame#cameraCard { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #17313a,stop:1 #122530); border: 1px solid #2c4e56; border-radius: 16px; }
QFrame#controlBar { background: #142332; border: 1px solid #2b3d4d; border-radius: 16px; }
QLabel#brand { font-size: 22pt; font-weight: 700; letter-spacing: -0.8px; }
QLabel#eyebrow { color: #67dfc5; font-size: 8pt; font-weight: 600; letter-spacing: 2px; }
QLabel#title { font-size: 27pt; font-weight: 600; letter-spacing: -0.8px; }
QLabel#sectionTitle { font-size: 12pt; font-weight: 600; }
QLabel#muted { color: #9aafbf; font-size: 10pt; }
QLabel#small { color: #9aafbf; font-size: 9pt; }
QLabel#badge { color: #b2c7d5; background: #1b3040; border: 1px solid #304757; border-radius: 10px; padding: 7px 10px; font-size: 9pt; }
QLabel#status { color: #9aafbf; font-size: 9pt; padding: 2px 4px; }
QPushButton { background: #1c3041; border: 1px solid #324b5d; border-radius: 11px; padding: 10px 13px; font-weight: 600; }
QPushButton:hover { background: #254153; border-color: #527985; }
QPushButton:pressed { background: #183e43; border-color: #67dfc5; }
QPushButton:disabled { color: #748a9b; background: #132331; border-color: #263847; }
QPushButton:focus { border: 2px solid #67dfc5; padding: 9px 12px; }
QPushButton#primary { background: #67dfc5; color: #082e2b; border-color: #67dfc5; }
QPushButton#primary:hover { background: #8eecd6; border-color: #8eecd6; }
QPushButton#primary:pressed { background: #46bea7; }
QPushButton#primary:disabled { background: #1d4746; color: #89b1ac; border-color: #295956; }
QPushButton#nav, QPushButton#navInactive { text-align: left; padding: 13px 12px; }
QPushButton#nav { background: #173d43; border: 1px solid #2c6062; color: #a7f2df; }
QPushButton#navInactive { background: transparent; border: 1px solid transparent; color: #9aafbf; }
QPushButton#navInactive:hover { background: #1a2c3b; color: #edf4f8; }
QPushButton#quiet { background: transparent; border: 1px solid transparent; color: #b2c7d5; font-weight: 500; }
QPushButton#quiet:hover { background: #1c3041; border-color: #324b5d; color: #edf4f8; }
QPushButton#quiet:focus, QPushButton#nav:focus, QPushButton#navInactive:focus { border: 2px solid #67dfc5; }
QPushButton#direction { min-width: 42px; min-height: 42px; padding: 3px; border-radius: 14px; }
QPushButton#direction:pressed { background: #25675e; border-color: #67dfc5; }
QSlider { background: transparent; }
QSlider::groove:horizontal { height: 4px; background: #334d60; border: 0; border-radius: 2px; }
QSlider::handle:horizontal { background: #edf4f8; border: 3px solid #67dfc5; width: 10px; height: 10px; margin: -6px 0; border-radius: 8px; }
QComboBox { background: #1a2e3f; color: #edf4f8; border: 1px solid #324b5d; border-radius: 10px; padding: 9px; }
QComboBox:focus { border-color: #67dfc5; }
QComboBox:disabled { color: #748a9b; border-color: #263847; }
QComboBox::drop-down { border: 0; width: 22px; }
QComboBox QAbstractItemView { background: #172b3b; color: #edf4f8; selection-background-color: #24534f; padding: 6px; }
QLineEdit, QListWidget { background: #0e1a27; color: #edf4f8; border: 1px solid #324b5d; border-radius: 10px; padding: 10px; }
QLineEdit:focus, QListWidget:focus { border-color: #67dfc5; }
QLineEdit:disabled { color: #9aafbf; border-color: #263847; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #385466; border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget::item { padding: 12px; border-radius: 6px; }
QListWidget::item:selected { background: #204d4c; color: #d4fff2; }
QCheckBox { spacing: 9px; color: #b2c7d5; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid #547082; background: #132331; }
QCheckBox::indicator:checked { background: #67dfc5; border: 4px solid #224d49; }
QCheckBox::indicator:focus { border-color: #edf4f8; }
QMenu { background: #142332; color: #edf4f8; border: 1px solid #324b5d; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 10px 20px; border-radius: 6px; }
QMenu::item:selected { background: #204d4c; }
QMessageBox { background: #142332; }
QToolTip { color: #edf4f8; background: #20394a; border: 1px solid #527985; border-radius: 6px; padding: 8px; }
"""


class VolumeSlider(QSlider):
    """Native slider interaction with a consistently thin, rounded painted track."""
    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setFixedHeight(26)

    def paintEvent(self, event):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(QStyle.ComplexControl.CC_Slider, option,
                                            QStyle.SubControl.SC_SliderHandle, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        middle = self.height() / 2
        left, right = handle.width() / 2, self.width() - handle.width() / 2
        center = handle.x() + handle.width() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#334d60'))
        painter.drawRoundedRect(QRectF(left, middle - 2, right - left, 4), 2, 2)
        painter.setBrush(QColor(ACCENT if self.isEnabled() else MUTED))
        start, end = (center, right) if option.upsideDown else (left, center)
        painter.drawRoundedRect(QRectF(start, middle - 2, max(0, end - start), 4), 2, 2)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(MUTED), 1))
            painter.drawEllipse(QRectF(center - 10, middle - 10, 20, 20))
        painter.setPen(QPen(QColor(ACCENT if self.isEnabled() else MUTED), 3))
        painter.setBrush(QColor(INK))
        painter.drawEllipse(QRectF(center - 6, middle - 6, 12, 12))


def label(text, name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text, glyph=None, name=None):
    widget = QPushButton(text)
    if glyph:
        widget.setIcon(icon(glyph))
        widget.setIconSize(QSize(19, 19))
    if name:
        widget.setObjectName(name)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def make_phone_widget(parent):
    from home_gateway import connector_widget
    return connector_widget(parent, embedded=True)


def make_owlet_poller():
    from owlet_overlay import StatusPoller
    from owlet_windows import load_address
    return StatusPoller(load_address)


class VideoSurface(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(360, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.pixmap = None
        self.compact = False
        self.owlet = None
        self.frame_updated_at = None
        self.clock_text = self.owlet_updated_text = ''
        self.title = 'Camera stopped'
        self.description = 'Press Start to view.'

    def clear(self, title='Camera stopped', description='Press Start to view.'):
        self.pixmap = None
        self.owlet = None
        self.frame_updated_at = None
        self.clock_text = self.owlet_updated_text = ''
        self.title, self.description = title, description
        self.update()

    def show_frame(self, frame, received_at=None):
        data, width, height, stride = frame
        image = QImage(data, width, height, stride, QImage.Format.Format_RGB888)
        self.pixmap = QPixmap.fromImage(image)
        self.frame_updated_at = time.monotonic() if received_at is None else received_at
        self.refresh_indicators()
        self.update()

    def show_owlet(self, value):
        self.owlet = dict(value) if value is not None and value.get('availability') != 'offline' else None
        self.refresh_indicators()
        self.update()

    def refresh_indicators(self):
        clock = video_clock(self.frame_updated_at) if self.pixmap is not None else ''
        source = owlet_freshness(self.owlet) if self.pixmap is not None else ''
        if (clock, source) != (self.clock_text, self.owlet_updated_text):
            self.clock_text, self.owlet_updated_text = clock, source
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor('#142936'))
        background.setColorAt(1, QColor('#09121d'))
        painter.setBrush(QColor('#070e16') if self.pixmap is not None else background)
        painter.drawRoundedRect(self.rect(), 20, 20)
        if self.pixmap is not None:
            size = self.pixmap.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            x, y = (self.width() - size.width()) // 2, (self.height() - size.height()) // 2
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(x, y, size.width(), size.height(), self.pixmap)
            if self.owlet is not None:
                # Display only: the decoded source pixmap and capture frames stay untouched.
                width = max(1, min(size.width() - 16, 780))
                left, top, height = x + (size.width() - width) // 2, y + 8, 58
                painter.setBrush(QColor(8, 15, 20, 205))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(left, top, width, height, 8, 8)
                painter.setPen(QColor(INK if self.owlet.get('availability') == 'live' else MUTED))
                painter.setFont(QFont('Segoe UI', 10 if width < 500 else 12, QFont.Weight.DemiBold))
                parts = display_parts(self.owlet)
                cell = width // 3
                for index, part in enumerate(parts):
                    text = painter.fontMetrics().elidedText(part, Qt.TextElideMode.ElideRight, max(1, cell - 12))
                    painter.drawText(left + index * cell + 6, top, cell - 12, 34, Qt.AlignmentFlag.AlignCenter, text)
                painter.setPen(QColor(MUTED))
                painter.setFont(QFont('Segoe UI', 8 if width < 500 else 10))
                freshness = painter.fontMetrics().elidedText(self.owlet_updated_text, Qt.TextElideMode.ElideRight, max(1, width - 12))
                painter.drawText(left + 6, top + 34, width - 12, 20, Qt.AlignmentFlag.AlignCenter, freshness)
            if self.clock_text and size.width() > 32 and size.height() > 100:
                painter.setFont(QFont('Segoe UI', 8 if self.compact or size.width() < 500 else 10, QFont.Weight.DemiBold))
                width = min(size.width() - 16, painter.fontMetrics().horizontalAdvance(self.clock_text) + 16)
                left, top = x + 8, y + size.height() - 36
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(8, 15, 20, 205))
                painter.drawRoundedRect(left, top, width, 28, 6, 6)
                painter.setPen(QColor(INK))
                text = painter.fontMetrics().elidedText(self.clock_text, Qt.TextElideMode.ElideRight, max(1, width - 12))
                painter.drawText(left + 6, top, width - 12, 28, Qt.AlignmentFlag.AlignCenter, text)
        else:
            middle = self.height() // 2
            if self.compact:
                painter.setPen(QColor(INK))
                painter.setFont(QFont('Segoe UI', 11, QFont.Weight.DemiBold))
                title = painter.fontMetrics().elidedText(self.title, Qt.TextElideMode.ElideRight, max(1, self.width() - 24))
                painter.drawText(12, middle - 25, self.width() - 24, 28, Qt.AlignmentFlag.AlignCenter, title)
                painter.setPen(QColor(MUTED))
                painter.setFont(QFont('Segoe UI', 8))
                text = painter.fontMetrics().elidedText(self.description, Qt.TextElideMode.ElideRight, max(1, self.width() - 24))
                painter.drawText(12, middle + 6, self.width() - 24, 24, Qt.AlignmentFlag.AlignCenter, text)
                return
            painter.setPen(QPen(QColor('#244450'), 1))
            painter.setBrush(QColor('#16343c'))
            painter.drawRoundedRect(QRectF(self.width() / 2 - 44, middle - 113, 88, 88), 26, 26)
            icon('camera', ACCENT, 42).paint(painter, self.width() // 2 - 21, middle - 90, 42, 42)
            painter.setPen(QColor(INK))
            painter.setFont(QFont('Segoe UI', 19, QFont.Weight.DemiBold))
            painter.drawText(10, middle - 6, self.width() - 20, 36, Qt.AlignmentFlag.AlignCenter, self.title)
            painter.setPen(QColor(MUTED))
            painter.setFont(QFont('Segoe UI', 10))
            painter.drawText(10, middle + 37, self.width() - 20, 30, Qt.AlignmentFlag.AlignCenter, self.description)


class ViewerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Local Arenti Baby Cam')
        self.setWindowIcon(app_icon())
        self.resize(1280, 820)
        self.setMinimumSize(1040, 700)
        self.mailbox = None
        self.camera = None
        self.movement = None
        self.stopping = False
        self.closing = False
        self.muted = True
        self.audio_sink = None
        self.audio_device = None
        self.audio_output = None
        self.audio_unavailable = False
        self.started_at = None
        self.last_stats = {}
        self.total_move_requests = 0
        self.end_reason = None
        self._releasing_input = False
        self.restart_after_stop = False
        self.preview_recovery = PreviewRecovery()
        self.auto_quality = 'high'
        self.autoplay_on_show = False  # Enabled only by the normal application entry point.
        self.manually_stopped = False
        self.profile_choices = None
        self.phone_widget = None
        self.background_started = False
        self.tray = None
        self.exiting = False
        self.managed_runtime = False
        self.activation_server = None
        self._tray_tip = None
        self.phone_retry_done = False
        self.phone_retry_attempts = 0
        self.pip_window = None
        self.owlet = None
        self.build_ui()
        # Keep the device monitor alive: an already-created sink remains bound
        # to its original endpoint when Windows changes its default output.
        self.media_devices = QMediaDevices(self)
        self.media_devices.audioOutputsChanged.connect(self.refresh_audio_output)
        self.audio_retry_timer = QTimer(self)
        self.audio_retry_timer.setInterval(1000)
        self.audio_retry_timer.timeout.connect(self.refresh_audio_output)
        self.audio_retry_timer.start()
        self.owlet_requested = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(33)
        self.phone_retry_timer = QTimer(self)
        self.phone_retry_timer.setSingleShot(True)
        self.phone_retry_timer.timeout.connect(self.retry_phone_start)
        self.owlet_timer = QTimer(self)
        self.owlet_timer.setInterval(1000)
        self.owlet_timer.timeout.connect(self.refresh_owlet)
        QApplication.instance().installEventFilter(self)

    def build_ui(self):
        root = QWidget(objectName='root')
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = QFrame(objectName='sidebar')
        self.sidebar.setFixedWidth(208)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(20, 30, 20, 24)
        side.setSpacing(12)
        brand_icon = QLabel()
        brand_icon.setPixmap(app_icon().pixmap(46, 46))
        side.addWidget(brand_icon)
        side.addWidget(label('Local', 'brand'))
        side.addWidget(label('BABY CAM', 'eyebrow'))
        side.addSpacing(24)
        self.live_nav = button('  Live view', 'camera', 'nav')
        self.live_nav.clicked.connect(self.show_viewer)
        side.addWidget(self.live_nav)
        self.phone_nav = button('  Phone access', 'shield', 'navInactive')
        self.phone_nav.setToolTip('Funnel status, Start/Stop phone access, pair or revoke a phone. No preview starts.')
        self.phone_nav.clicked.connect(self.show_phone_access)
        side.addWidget(self.phone_nav)
        setup = button('  Camera setup', 'camera', 'navInactive')
        setup.clicked.connect(self.configure_camera)
        side.addWidget(setup)
        side.addSpacing(18)
        side.addWidget(label('YOUR CAMERA', 'eyebrow'))
        card = QFrame(objectName='cameraCard')
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 14, 14, 14)
        card_layout.setSpacing(7)
        card_layout.addWidget(label('Arenti D3', 'sectionTitle'))
        card_layout.addWidget(label('Local Wi-Fi camera', 'small'))
        self.side_state = label('●  Ready to connect', 'small')
        card_layout.addWidget(self.side_state)
        side.addWidget(card)
        side.addStretch()
        self.startup_toggle = QCheckBox('Start with Windows')
        self.startup_toggle.setToolTip('Start in the system tray with phone access ready. Never auto-start live preview.')
        self.startup_toggle.setEnabled(desktop_autostart.available())
        if desktop_autostart.available():
            try:
                self.startup_toggle.setChecked(desktop_autostart.enabled())
            except Exception:
                self.startup_toggle.setToolTip('Install the app first to manage its per-user Startup shortcut.')
        self.startup_toggle.toggled.connect(self.set_startup)
        side.addWidget(self.startup_toggle)
        about = button('  About', 'camera', 'quiet')
        about.clicked.connect(self.about)
        side.addWidget(about)
        side.addWidget(label(f'Windows desktop · v{VERSION}', 'small'))
        outer.addWidget(self.sidebar)

        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(26, 26, 26, 18)
        layout.setSpacing(18)
        self.header = QWidget()
        head = QHBoxLayout(self.header)
        head.setContentsMargins(0, 0, 0, 0)
        titles = QVBoxLayout()
        titles.setSpacing(5)
        titles.addWidget(label('CAMERA OVERVIEW', 'eyebrow'))
        titles.addWidget(label('Live view', 'title'))
        head.addLayout(titles)
        head.addStretch()
        self.start_button = button('  Start live view', 'play', 'primary')
        self.start_button.setIcon(icon('play', '#082e2b'))
        self.start_button.setMinimumHeight(42)
        self.start_button.clicked.connect(self.start_live)
        self.stop_button = button('  Stop', 'stop')
        self.stop_button.setMinimumHeight(42)
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_live)
        head.addWidget(self.start_button)
        head.addWidget(self.stop_button)
        self.popout_button = button('  Pop-out', 'pip')
        self.popout_button.setAccessibleName('Pop out camera')
        self.popout_button.setToolTip('Compact camera window that stays above other apps. Does not start preview automatically.')
        self.popout_button.setMinimumHeight(42)
        self.popout_button.clicked.connect(self.toggle_popout)
        head.addWidget(self.popout_button)
        layout.addWidget(self.header)

        body = QHBoxLayout()
        body.setSpacing(18)
        player_column = QVBoxLayout()
        self.player_column = player_column
        player_column.setSpacing(12)
        self.video = VideoSurface()
        self.video_minimum = self.video.minimumSize()
        player_column.addWidget(self.video, 1)
        self.pip_placeholder = QFrame(objectName='panel')
        self.pip_placeholder.setMinimumSize(360, 280)
        placeholder = QVBoxLayout(self.pip_placeholder)
        placeholder.addStretch()
        placeholder.addWidget(label('Video is in the always-on-top window.', 'sectionTitle', True))
        back = button('Return video here', 'pip')
        back.clicked.connect(lambda: self.dock_video(restore_main=True))
        placeholder.addWidget(back)
        placeholder.addStretch()
        player_column.addWidget(self.pip_placeholder, 1)
        self.pip_placeholder.hide()
        audio_bar = QFrame(objectName='controlBar')
        audio_layout = QHBoxLayout(audio_bar)
        audio_layout.setContentsMargins(13, 9, 13, 9)
        self.mute_button = button('  Muted', 'muted', 'quiet')
        self.mute_button.setToolTip('Toggle live camera audio. Playback always starts muted.')
        self.mute_button.clicked.connect(self.toggle_mute)
        audio_layout.addWidget(self.mute_button)
        self.volume = VolumeSlider()
        self.volume.setRange(0, 100)
        self.volume.setValue(65)
        self.volume.setFixedWidth(90)
        self.volume.setAccessibleName('Live audio volume')
        self.volume.valueChanged.connect(self.volume_changed)
        audio_layout.addWidget(self.volume)
        audio_layout.addStretch()
        self.quality = QComboBox()
        self.quality.setAccessibleName('Video quality')
        self.quality.setMinimumWidth(116)
        self.quality.addItem('Auto · High', 'auto')
        self.quality.addItem('High quality', 'high')
        self.quality.addItem('Low quality', 'low')
        self.quality.model().item(2).setEnabled(False)
        self.quality.currentIndexChanged.connect(self.change_quality)
        self.quality.setToolTip('Auto starts High and uses Low after two playback failures when available. '
                               'Healthy video stays on its current stream. High/Low are manual overrides.')
        audio_layout.addWidget(self.quality)
        self.resolution = label('Video stopped', 'small')
        full = button('', 'expand', 'quiet')
        full.setToolTip('Full screen · Escape to leave')
        full.setAccessibleName('Toggle full screen')
        full.clicked.connect(self.toggle_fullscreen)
        audio_layout.addWidget(full)
        player_column.addWidget(audio_bar)
        capture_bar = QFrame(objectName='panel')
        capture_layout = QVBoxLayout(capture_bar)
        capture_layout.setContentsMargins(13, 9, 13, 9)
        capture_buttons = QHBoxLayout()
        self.snapshot_button = button('  Screenshot', 'camera')
        self.snapshot_button.setToolTip('Save the next full-resolution camera frame as a local PNG.')
        self.snapshot_button.setEnabled(False)
        self.snapshot_button.clicked.connect(self.take_snapshot)
        self.record_button = button('  Record', 'record')
        self.record_button.setToolTip('Save local MP4 video and camera microphone audio, even while playback is muted.')
        self.record_button.setEnabled(False)
        self.record_button.clicked.connect(self.toggle_recording)
        capture_buttons.addWidget(self.snapshot_button)
        capture_buttons.addWidget(self.record_button)
        self.capture_status = label('Not recording. MP4 includes camera audio even when playback is muted.', 'small', True)
        capture_layout.addLayout(capture_buttons)
        capture_buttons.addStretch()
        capture_layout.addWidget(self.capture_status)
        player_column.addWidget(capture_bar)
        body.addLayout(player_column, 1)

        self.right_panel = QScrollArea()
        self.right_panel.setWidgetResizable(True)
        self.right_panel.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.right_panel.setFixedWidth(224)
        right_content = QWidget(objectName='root')
        self.right_panel.setWidget(right_content)
        right = QVBoxLayout(right_content)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(16)
        ptz_card = QFrame(objectName='panel')
        ptz = QVBoxLayout(ptz_card)
        ptz.setContentsMargins(19, 21, 19, 20)
        ptz.setSpacing(10)
        ptz.addWidget(label('CAMERA POSITION', 'eyebrow'))
        ptz.addWidget(label('Pan & tilt', 'sectionTitle'))
        ptz.addWidget(label('Hold to move. Release to stop.', 'small', True))
        ptz.addSpacing(10)
        pad = QGridLayout()
        pad.setSpacing(7)
        self.direction_buttons = {}
        for direction, row, column in (('up', 0, 1), ('left', 1, 0), ('right', 1, 2), ('down', 2, 1)):
            arrow = button('', direction, 'direction')
            arrow.setAccessibleName(f'Move camera {direction}')
            arrow.setToolTip(f'Hold to move {direction}')
            arrow.setEnabled(False)
            arrow.pressed.connect(lambda d=direction: self.hold(d))
            arrow.released.connect(self.release)
            self.direction_buttons[direction] = arrow
            pad.addWidget(arrow, row, column)
        self.motor_stop = button('', 'stop', 'direction')
        self.motor_stop.setIcon(icon('stop', ACCENT))
        self.motor_stop.setToolTip('Stop camera movement')
        self.motor_stop.setAccessibleName('Stop camera movement')
        self.motor_stop.setEnabled(False)
        self.motor_stop.clicked.connect(lambda: self.release(force=True))
        pad.addWidget(self.motor_stop, 1, 1)
        ptz.addLayout(pad)
        ptz.addSpacing(10)
        self.ptz_status = label('Available during live view.', 'small', True)
        self.ptz_status.setMinimumHeight(35)
        ptz.addWidget(self.ptz_status)
        right.addWidget(ptz_card)
        connection_card = QFrame(objectName='panel')
        details = QVBoxLayout(connection_card)
        details.setContentsMargins(18, 19, 18, 19)
        details.setSpacing(10)
        details.addWidget(label('SESSION', 'eyebrow'))
        details.addWidget(label('Connection', 'sectionTitle'))
        details.addWidget(label('Camera → this PC', 'muted'))
        self.resolution.setWordWrap(True)
        details.addWidget(self.resolution)
        details.addSpacing(5)
        self.recording_badge = label('●  Not recording', 'badge')
        details.addWidget(self.recording_badge)
        right.addWidget(connection_card)
        right.addStretch()
        body.addWidget(self.right_panel)
        layout.addLayout(body, 1)
        self.status = label('Stopped.', 'status', True)
        layout.addWidget(self.status)
        self.footer = label('', 'small')
        layout.addWidget(self.footer)
        self.pages = QStackedWidget()
        self.pages.addWidget(main)
        self.pages.currentChanged.connect(self.update_navigation)
        outer.addWidget(self.pages, 1)

    def update_navigation(self, index):
        for control, selected in ((self.live_nav, index == 0), (self.phone_nav, index != 0)):
            control.setObjectName('nav' if selected else 'navInactive')
            control.setAccessibleDescription('Current page' if selected else '')
            control.style().unpolish(control)
            control.style().polish(control)

    def set_startup(self, enabled):
        try:
            desktop_autostart.set_enabled(enabled)
        except Exception as exc:
            self.startup_toggle.blockSignals(True)
            self.startup_toggle.setChecked(not enabled)
            self.startup_toggle.blockSignals(False)
            QMessageBox.warning(self, 'Windows startup', f'Could not update the app Startup shortcut ({type(exc).__name__}).')

    def ensure_phone_widget(self):
        if self.phone_widget is None:
            self.phone_widget = make_phone_widget(self.pages)
            self.pages.addWidget(self.phone_widget)
            control = getattr(self.phone_widget, 'start', None)
            if control is not None:
                control.clicked.connect(self.cancel_phone_retry)
        return self.phone_widget

    def configure_camera(self):
        if self.camera is not None or self.stopping or (self.phone_widget and self.phone_widget.server):
            QMessageBox.information(self, 'Camera setup', 'Stop live view and stop the phone connector before changing cameras.')
            return
        from camera_setup import show_setup
        show_setup(self)

    def start_background(self):
        if self.background_started or self.exiting:
            return
        self.background_started = True
        self.retry_phone_start()

    def cancel_phone_retry(self):
        self.phone_retry_done = True
        self.phone_retry_timer.stop()

    def retry_phone_start(self):
        if self.exiting or self.phone_retry_done:
            return
        try:
            phone = self.ensure_phone_widget()
            if not phone.server:
                phone.toggle()
            if phone.server:
                self.cancel_phone_retry()
        except Exception as exc:
            self.status.setText(f'Phone access could not start ({type(exc).__name__}). Open Phone access to review it.')
        if not self.phone_retry_done:
            # Login may run before Wi-Fi has its LAN address. Retry listeners,
            # not video; any explicit Start/Stop click cancels startup retries.
            delay = min(30, 2 << min(self.phone_retry_attempts, 4))
            self.phone_retry_attempts += 1
            self.phone_retry_timer.start(delay * 1000)
        self.update_tray()

    def show_viewer(self):
        self.restore_chrome()
        self.pages.setCurrentIndex(0)
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.start_button.setFocus()
        self.maybe_autoplay()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.maybe_autoplay)

    def maybe_autoplay(self):
        if (self.autoplay_on_show and not self.manually_stopped and self.isVisible()
                and not self.isMinimized() and self.pages.currentIndex() == 0
                and not self.closing and not self.exiting and not self.stopping
                and self.camera is None and not self.preview_recovery.enabled):
            self.start_live()

    def show_phone_access(self):
        self.release()
        self.restore_chrome()
        try:
            self.pages.setCurrentWidget(self.ensure_phone_widget())
        except Exception as exc:
            self.status.setText(f'Phone access panel unavailable ({type(exc).__name__}).')
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def restore_chrome(self):
        for widget in (self.sidebar, self.right_panel, self.header, self.footer):
            widget.setVisible(True)

    def setup_tray(self):
        self.managed_runtime = True
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray_menu = QMenu(self)
        self.tray_menu.addAction('Open live viewer', self.show_viewer)
        self.tray_menu.addAction('Phone access / pair phone', self.show_phone_access)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction('Exit — stop phone access too', self.request_exit)
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self.show_viewer()
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.update_tray()
        return True

    def update_tray(self):
        if self.tray:
            phone_on = bool(self.phone_widget is not None and self.phone_widget.server)
            state = 'preview reconnecting' if self.preview_recovery.pending else 'preview stopping' if self.stopping else 'preview live' if self.camera else 'preview idle'
            tip = 'Local Arenti Baby Cam · phone access ' + ('on' if phone_on else 'off') + ' · ' + state
            if tip != self._tray_tip:
                self.tray.setToolTip(tip)
                self._tray_tip = tip

    def hide_to_tray(self):
        self.release()
        self.dock_video()
        self.stop_live(manual=False)
        self.hide()
        self.update_tray()

    def shutdown_background(self):
        self.cancel_phone_retry()
        if self.phone_widget is not None:
            self.phone_widget.stop()

    def request_exit(self):
        self.exiting = True
        self.restart_after_stop = False
        self.preview_recovery.stop()
        self.shutdown_background()
        self.close()

    def toggle_popout(self):
        if self.closing or self.exiting:
            return
        if self.pip_window is not None:
            self.dock_video(restore_main=True)
            return
        self.release()
        if self.isFullScreen():
            self.toggle_fullscreen()
        self.pip_window = PopoutWindow(self, button, icon)
        self.player_column.removeWidget(self.video)
        self.video.setMinimumSize(160, 90)
        self.video.compact = True
        self.pip_window.video_layout.addWidget(self.video, 1)
        self.video.show()
        self.pip_placeholder.show()
        self.popout_button.setText('  Return video')
        self.popout_button.setAccessibleName('Return video to main viewer')
        screen = self.screen() or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.pip_window.move(max(area.left(), area.right() - self.pip_window.width() - 16),
                max(area.top(), area.bottom() - self.pip_window.height() - 40))
        self.pip_window.show()
        self.pip_window.raise_()
        self.pip_window.activateWindow()

    def dock_video(self, *, close_window=True, restore_main=False):
        popup = self.pip_window
        if popup is None:
            return
        self.release()
        self.pip_window = None
        popup.video_layout.removeWidget(self.video)
        self.video.compact = False
        self.video.setMinimumSize(self.video_minimum)
        self.player_column.insertWidget(0, self.video, 1)
        self.video.show()
        self.pip_placeholder.hide()
        self.popout_button.setText('  Pop-out')
        self.popout_button.setAccessibleName('Pop out camera')
        if close_window:
            popup.close()
        if restore_main:
            self.show_viewer()

    def start_live(self):
        if self.camera is not None or self.closing or self.exiting:
            return
        self.manually_stopped = False
        self.auto_quality = 'high'
        self.preview_recovery.start(time.monotonic())
        self._start_live_attempt(manual=True)

    def _start_live_attempt(self, manual=False):
        if self.camera is not None or self.closing or self.exiting or not self.preview_recovery.enabled:
            return
        if not protected_store.ACTIVE.exists():
            self.preview_recovery.stop()
            self.stop_button.setEnabled(False)
            QMessageBox.warning(self, 'Local configuration missing',
                'The protected camera configuration is missing. Run the local desktop installer first.\n\n'
                'No cloud sign-in or fallback connection will be attempted.')
            return
        if manual:
            self.muted = True
        self.update_mute_label()
        self.mailbox = MediaMailbox()
        self.mailbox.status(reconnects=self.preview_recovery.attempts)
        self.timer.setInterval(33)
        quality = self.quality.currentData()
        if quality == 'auto':
            quality = self.auto_quality
        self.quality.setItemText(0, 'Auto · ' + self.auto_quality.title())
        self.camera = CameraWorker(self.mailbox, quality=quality)
        self.movement = None
        self.stopping = False
        self.end_reason = None
        self.started_at = time.monotonic()
        self.preview_recovery.begin_attempt(self.started_at)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.quality.setEnabled(False)
        self.side_state.setText('●  Connecting…')
        self.video.clear('Connecting…', '')
        self.status.setText('Connecting…')
        self.camera.start()
        if not self.muted:
            self.setup_audio()

    def stop_live(self, _checked=False, *, manual=True):
        if manual:
            self.manually_stopped = True
        self.preview_recovery.stop()
        self.restart_after_stop = False
        self.end_reason = None
        self._stop_live()
        if self.camera is None:
            self.stop_button.setEnabled(False)
            self.side_state.setText('●  Ready to connect')
            self.video.clear()
            self.status.setText('Stopped. You can start another live view.')

    def _stop_live(self):
        self.stop_owlet()
        if self.camera is None:
            return
        self.release()
        self.stopping = True
        self.stop_button.setEnabled(self.preview_recovery.pending)
        if self.movement:
            self.movement.request_stop()
        self.camera.request_stop()
        self.reset_audio()
        self.video.clear('Stopping…', '')
        self.status.setText('Stopping safely…')
        self.side_state.setText('●  Stopping…')
        self.set_directions(False)
        self.snapshot_button.setEnabled(False)
        self.record_button.setEnabled(False)

    def recover_preview(self, reason):
        if not self.preview_recovery.enabled or self.stopping or self.closing or self.exiting:
            return
        self.end_reason = reason
        self.preview_recovery.failed(time.monotonic())
        if (self.quality.currentData() == 'auto' and self.preview_recovery.attempts >= 2
                and self.profile_choices and len(self.profile_choices) > 1):
            self.auto_quality = 'low'
        self._stop_live()
        if self.preview_recovery.pending:
            self.status.setText('Video interrupted. Finishing this session before reconnecting; Stop cancels.')

    def refresh_preview_retry(self):
        if not self.preview_recovery.pending or self.closing or self.exiting:
            return
        now = time.monotonic()
        if self.preview_recovery.due(now):
            self._start_live_attempt()
        else:
            remaining = max(1, math.ceil(self.preview_recovery.retry_at - now))
            self.stop_button.setEnabled(True)
            self.side_state.setText('●  Reconnecting…')
            self.status.setText(f'Reconnecting in {remaining}s (attempt {self.preview_recovery.attempts}). Stop cancels.')
            self.video.clear('Reconnecting…', 'Stop cancels automatic recovery.')

    def refresh_owlet(self):
        if self.owlet is not None and self.camera is not None and not self.stopping:
            try:
                self.video.show_owlet(self.owlet.snapshot())
            except Exception:
                self.video.show_owlet(empty_snapshot())

    def stop_owlet(self):
        self.owlet_timer.stop()
        if self.owlet is not None:
            try:
                self.owlet.stop()
            except Exception:
                pass
            self.owlet = None
        self.owlet_requested = False
        self.video.show_owlet(None)

    def choose_capture_path(self, suffix):
        self.release()
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.MoviesLocation)
        folder = Path(base or str(Path.home() / 'Videos')) / 'Arenti Local Viewer'
        try:
            folder.mkdir(parents=True, exist_ok=True)
            name = 'Arenti-D3-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + suffix
            title = 'Save full-resolution camera screenshot' if suffix == '.png' else 'Save MP4 video + camera microphone audio'
            filename, _ = QFileDialog.getSaveFileName(self, title, str(folder / name),
                'PNG image (*.png)' if suffix == '.png' else 'MP4 video (*.mp4)')
            return filename or None
        except Exception as exc:
            self.capture_status.setText(f'Cannot open the capture folder ({type(exc).__name__}).')
            return None

    def take_snapshot(self):
        if not self.camera or self.stopping or not self.mailbox.recent():
            return
        path = self.choose_capture_path('.png')
        if path and self.camera and not self.stopping:
            try:
                self.camera.request_snapshot(path)
                self.snapshot_button.setEnabled(False)
            except Exception as exc:
                self.capture_status.setText(f'Screenshot not started ({type(exc).__name__}). Choose a new local file.')

    def toggle_recording(self):
        if not self.camera or self.stopping:
            return
        state = self.mailbox.snapshot().get('recordingState', 'idle')
        if state in ('waiting', 'recording'):
            self.camera.stop_recording()
            return
        if state == 'saving' or not self.mailbox.recent():
            return
        path = self.choose_capture_path('.mp4')
        if path and self.camera and not self.stopping:
            try:
                self.camera.start_recording(path)
            except Exception as exc:
                self.capture_status.setText(f'Recording not started ({type(exc).__name__}). Choose a new local file.')

    def refresh_capture(self, stats):
        state = stats.get('recordingState', 'idle')
        snapshot = stats.get('snapshotState', 'idle')
        snapshot_latest = stats.get('snapshotUpdatedAt', 0) >= stats.get('recordingUpdatedAt', 0)
        live = bool(self.camera and not self.stopping and self.mailbox and self.mailbox.recent())
        self.snapshot_button.setEnabled(live and snapshot not in ('waiting', 'saving'))
        self.record_button.setEnabled((live or state in ('waiting', 'recording')) and not self.stopping and state != 'saving')
        active = state in ('waiting', 'recording')
        self.record_button.setText('  Stop recording' if active else '  Saving…' if state == 'saving' else '  Record')
        self.record_button.setIcon(icon('stop' if active else 'record', '#f0a49c' if active else INK))
        self.recording_badge.setText('●  Recording locally' if active else '●  Saving recording' if state == 'saving' else '●  Not recording')
        self.recording_badge.setStyleSheet('color: #f2b4ad; background: #3d2828;' if active or state == 'saving' else '')
        message = None
        if state == 'recording':
            elapsed = max(0, int(time.monotonic() - stats.get('recordingStartedAt', time.monotonic())))
            message = f'Recording {elapsed // 60:02d}:{elapsed % 60:02d} · includes microphone audio.'
        elif state == 'waiting':
            message = 'Starting recording at the next video keyframe…'
        elif state == 'saving':
            message = 'Finalizing the MP4. Please wait…'
        elif state == 'error' and not snapshot_latest:
            message = f'Recording failed ({stats.get("recordingErrorClass", "Error")}). A partial file may remain.'
        elif snapshot == 'error' and snapshot_latest:
            message = f'Screenshot failed ({stats.get("snapshotErrorClass", "Error")}). Choose a new local file.'
        elif snapshot in ('waiting', 'saving'):
            message = 'Saving the next full-resolution camera frame…'
        elif snapshot == 'saved' and snapshot_latest:
            message = f'Screenshot saved: {Path(stats["snapshotPath"]).name}'
        elif state == 'saved':
            message = f'Recording saved: {Path(stats["recordingPath"]).name}'
        elif state == 'cancelled' or snapshot == 'cancelled':
            message = 'Capture cancelled before fresh video arrived.'
        if message:
            self.capture_status.setText(message)
        self.capture_status.setToolTip(str(stats.get('snapshotPath' if snapshot_latest and not active else 'recordingPath') or ''))

    def change_quality(self):
        self.release()
        self.auto_quality = 'high'
        self.quality.setItemText(0, 'Auto · High')
        if self.camera and not self.stopping:
            self.restart_after_stop = True
            self._stop_live()
            self.status.setText('Switching video quality…')
        elif self.camera is None and self.preview_recovery.pending:
            self.start_live()

    def update_quality_choices(self, profiles):
        if not profiles or profiles == self.profile_choices:
            return
        self.profile_choices = profiles
        high, low = profiles[0], profiles[-1]
        self.quality.blockSignals(True)
        self.quality.setItemText(1, f'High · {high["width"]} × {high["height"]}')
        self.quality.setItemText(2, f'Low · {low["width"]} × {low["height"]}')
        self.quality.model().item(2).setEnabled(len(profiles) > 1)
        self.quality.blockSignals(False)

    def hold(self, direction):
        if self.movement and not self.stopping and self.mailbox.recent() and self.isActiveWindow():
            if self.movement.hold(direction):
                self.ptz_status.setText(f'Moving {direction} · release to stop.')

    def release(self, force=False):
        if self._releasing_input:
            return
        self._releasing_input = True
        try:
            if self.movement:
                self.movement.release(force)
            # A button disabled/cancelled during a press can miss its release.
            # Never leave Qt's mouse capture attached to an arrow.
            grabber = QWidget.mouseGrabber()
            if grabber in self.direction_buttons.values():
                grabber.releaseMouse()
            for arrow in self.direction_buttons.values():
                arrow.setDown(False)
        finally:
            self._releasing_input = False

    def set_directions(self, enabled):
        supported = self.movement.directions if self.movement else []
        if not enabled:
            self.release()
        for direction, arrow in self.direction_buttons.items():
            arrow.setEnabled(enabled and direction in supported)
        self.motor_stop.setEnabled(enabled)

    def reset_audio(self):
        sink = self.audio_sink
        self.audio_sink = self.audio_device = self.audio_output = None
        if sink:
            sink.stop()
            sink.deleteLater()
        if self.mailbox:
            self.mailbox.discard_audio()

    def refresh_audio_output(self):
        # Poll as a fallback for backend/device events coalesced during a switch.
        # Intentional mute, Stop and shutdown never open an audio endpoint.
        if not self.muted and not self.closing and not self.exiting:
            self.setup_audio()

    def setup_audio(self):
        if self.camera is None or self.stopping or self.closing or self.exiting:
            return
        device = QMediaDevices.defaultAudioOutput()
        if (self.audio_sink and self.audio_device and self.audio_output == device
                and self.audio_sink.state() != QAudio.State.StoppedState
                and self.audio_sink.error() in (QAudio.Error.NoError, QAudio.Error.UnderrunError)):
            return
        self.reset_audio()
        format = QAudioFormat()
        format.setSampleRate(48000)
        format.setChannelCount(1)
        format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if device.isNull() or not device.isFormatSupported(format):
            self.audio_unavailable = True
            self.status.setText('Live video. A compatible Windows audio output is not available.')
            return
        self.audio_output = device
        self.audio_sink = QAudioSink(device, format, self)
        self.audio_sink.setBufferSize(19200)
        self.audio_sink.setVolume(0 if self.muted else self.volume.value() / 100)
        self.audio_device = self.audio_sink.start()
        self.audio_unavailable = (self.audio_device is None or self.audio_sink.state() == QAudio.State.StoppedState)
        if self.audio_unavailable:
            self.reset_audio()
            self.status.setText('Live video. Windows audio output unavailable; retrying automatically.')

    def update_mute_label(self):
        self.mute_button.setText('  Muted' if self.muted else '  Audio on')
        self.mute_button.setIcon(icon('muted' if self.muted else 'volume'))
        self.mute_button.setAccessibleName('Unmute camera audio' if self.muted else 'Mute camera audio')
        if self.pip_window is not None:
            self.pip_window.sync_controls()

    def toggle_mute(self):
        self.muted = not self.muted
        if not self.muted:
            self.setup_audio()
        else:
            self.reset_audio()
        if self.audio_sink:
            self.audio_sink.setVolume(0 if self.muted else self.volume.value() / 100)
        self.update_mute_label()

    def volume_changed(self, value):
        if self.audio_sink:
            self.audio_sink.setVolume(0 if self.muted else value / 100)

    def refresh(self):
        self.video.refresh_indicators()
        self.update_tray()
        if self.pip_window is not None:
            self.pip_window.sync_controls()
        if not self.camera:
            self.timer.setInterval(500)
            self.refresh_preview_retry()
            return
        stats = self.mailbox.snapshot()
        self.last_stats = stats
        self.update_quality_choices(stats.get('profiles'))
        self.refresh_capture(stats)
        if self.stopping:
            if not self.camera.alive() and (not self.movement or not self.movement.alive()):
                if self.movement:
                    self.total_move_requests += self.movement.moves
                self.camera = self.movement = None
                self.mailbox.clear()
                self.mailbox = None
                self.stopping = False
                self.stop_button.setEnabled(self.preview_recovery.pending)
                self.start_button.setEnabled(not self.closing)
                self.quality.setEnabled(not self.closing)
                self.video.clear()
                self.side_state.setText('●  Ready to connect')
                self.ptz_status.setText('Available during live view.')
                self.resolution.setText('LIVE AUDIO & VIDEO')
                if self.end_reason:
                    self.status.setText(f'Video interrupted ({self.end_reason}). Waiting to reconnect; Stop cancels.')
                else:
                    self.status.setText('Stopped. You can start another live view.')
                if self.closing:
                    self.close()
                elif self.restart_after_stop:
                    self.restart_after_stop = False
                    self.start_live()
                elif self.preview_recovery.pending:
                    self.refresh_preview_retry()
                else:
                    self.maybe_autoplay()
            return
        if not self.camera.alive():
            error = stats.get('errorClass') or 'ConnectionClosed'
            self.recover_preview(error)
            return
        if self.preview_recovery.stalled(time.monotonic(), stats.get('lastVideoAt', 0)):
            self.recover_preview('FrameTimeout' if stats.get('videoFrames') else 'StartupTimeout')
            return
        limit = min(self.audio_sink.bytesFree(), 16384) if self.audio_sink and self.audio_device and not self.muted else 0
        frame, pcm = self.mailbox.take(limit)
        if pcm and self.audio_device:
            if self.audio_device.write(pcm) < 0:
                self.audio_unavailable = True
                self.reset_audio()
        if frame:
            self.video.show_frame(frame, received_at=stats.get('lastVideoAt'))
            if not self.owlet_requested:
                self.owlet_requested = True
                try:
                    self.owlet = make_owlet_poller()
                    self.owlet.start()
                    self.owlet_timer.start()
                except Exception:
                    self.stop_owlet()
                    self.owlet_requested = True
                    self.video.show_owlet(empty_snapshot())
            self.refresh_owlet()
            self.update_quality_choices(stats.get('profiles'))
            self.quality.setEnabled(True)
            self.side_state.setText('●  Live on your network')
            self.resolution.setText(f'{stats["width"]} × {stats["height"]} · {stats["codec"].upper()}')
            elapsed = int(time.monotonic() - self.started_at)
            capture = 'Recording locally' if stats.get('recordingState') in ('waiting', 'recording') else 'No recording'
            audio_status = 'muted' if self.muted else ('unavailable; retrying' if self.audio_unavailable else 'on')
            self.status.setText(f'Live locally  ·  {elapsed // 60:02d}:{elapsed % 60:02d}  ·  Frames {stats["videoFrames"]:,}  ·  {capture}  ·  Audio {audio_status}')
            if self.movement is None:
                self.movement = MovementWorker(self.mailbox)
                self.movement.start()
        fresh = self.mailbox.recent()
        available = bool(fresh and self.movement and self.movement.ready)
        direction = self.movement.direction if self.movement else None
        if available and direction:
            held = self.direction_buttons.get(direction)
            if (held and self.isActiveWindow() and not self.isMinimized()
                    and held.isDown() and physical_hold_down()):
                self.movement.renew()
            else:
                self.release()
        self.set_directions(available)
        if not fresh and stats['videoFrames']:
            self.release()
            self.video.clear('Video interrupted', 'Waiting for fresh frames…')
            self.side_state.setText('●  Waiting for video…')
            self.ptz_status.setText('Paused until live video returns.')
            self.status.setText('Waiting for fresh video. Automatic recovery will reconnect if the interruption persists.')
        elif available and self.movement.direction is None:
            if any(button.isDown() for button in self.direction_buttons.values()):
                self.release()
            self.ptz_status.setText('Local controls ready. Hold an arrow.')
        elif self.movement and self.movement.error_class:
            self.ptz_status.setText('Pan/tilt unavailable. Video is independent.')

    def toggle_fullscreen(self):
        self.release()
        self.dock_video()
        full = not self.isFullScreen()
        for widget in (self.sidebar, self.right_panel, self.header, self.footer):
            widget.setVisible(not full)
        self.showFullScreen() if full else self.showNormal()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate,
                            QEvent.Type.MouseButtonRelease, QEvent.Type.UngrabMouse):
            self.release()
        elif event.type() == QEvent.Type.FocusOut and watched in self.direction_buttons.values():
            self.release()
        elif event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.release()
            if self.isFullScreen():
                self.toggle_fullscreen()
                return True
        elif event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.release()
        return super().eventFilter(watched, event)

    def about(self):
        self.release()
        QMessageBox.information(self, 'About Local Arenti Baby Cam',
            f'Local Arenti Baby Cam {VERSION}\n\n'
            'Live video and audio, pan/tilt, pop-out, screenshots and MP4 recording.\n'
            'Phone access runs while the Windows app is in the tray. Exit stops it.\n'
            'Windows startup opens the tray without starting desktop video.\n'
            'Opening the viewer starts video unless you pressed Stop in this app session.\n'
            'Auto quality starts High and falls back to Low after repeated interruptions.\n'
            'Recordings include camera audio even when playback is muted.\n'
            'Owlet is a reference display, not an alarm.\n\n'
            'Family APK access is managed under Phone access. Disabling it blocks family phones.\n'
            'The camera connection uses the home LAN; internet isolation is a separate router setting.\n\n'
            'Custom build, not an official Arenti app. Qt for Python and PyAV/FFmpeg; '
            'see THIRD-PARTY-NOTICES.txt.')

    def closeEvent(self, event):
        self.release()
        self.preview_recovery.stop()
        self.restart_after_stop = False
        if self.tray is not None and not self.exiting:
            self.hide_to_tray()
            event.ignore()
            return
        self.dock_video()
        if self.camera:
            self.closing = True
            self.stop_live()
            event.ignore()
        else:
            self.shutdown_background()
            self.timer.stop()
            if self.tray:
                self.tray.hide()
            if self.activation_server:
                self.activation_server.close()
            QApplication.instance().removeEventFilter(self)
            event.accept()
            if self.managed_runtime:
                QApplication.instance().quit()


def save_report(path, report):
    Path(path).write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--import-protected-config')
    parser.add_argument('--report')
    parser.add_argument('--background', action='store_true', help='Start in tray; phone access on, live preview off')
    parser.add_argument('--enable-startup', action='store_true', help='Enable this installed app in per-user Windows Startup')
    parser.add_argument('--gateway-self-test-report', help='Synthetic packaged gateway smoke check; never reads camera')
    parser.add_argument('--self-test', action='store_true', help='Offline UI check; never connects to a camera')
    parser.add_argument('--validate-live', type=int, choices=range(5, 61), metavar='5..60', help='Explicit A/V validation; no movement')
    parser.add_argument('--quality', choices=('auto', 'high', 'low'), default='auto', help='Auto fallback or an existing local stream; never encoder settings')
    parser.add_argument('--preview', help='Save ONLY the disconnected idle UI, never a camera frame')
    parser.add_argument('--preview-popout', help='Save ONLY the idle pop-out UI; no camera connection')
    parser.add_argument('--make-icon', help='Build application icon; no camera access')
    args = parser.parse_args()
    if (args.preview or args.preview_popout) and args.validate_live:
        parser.error('Idle UI preview cannot be combined with live validation')
    logging.disable(logging.CRITICAL)
    qInstallMessageHandler(lambda *_args: None)
    desktop_store.configure()
    if args.enable_startup or args.gateway_self_test_report:
        try:
            if args.enable_startup:
                result = desktop_autostart.set_enabled(True)
            else:
                from gateway.selftest import run
                result = run()
            code = 0
        except Exception as exc:
            result, code = {'errorClass': type(exc).__name__}, 1
        path = args.gateway_self_test_report or args.report
        if path:
            save_report(path, result)
        qInstallMessageHandler(None)
        return code
    if args.import_protected_config:
        try:
            result = desktop_store.import_encrypted(args.import_protected_config)
            code = 0
        except Exception as exc:
            result = {'errorClass': type(exc).__name__, 'plaintextSaved': False}
            code = 1
        if args.report:
            save_report(args.report, result)
        qInstallMessageHandler(None)
        return code
    app = QApplication(sys.argv[:1])
    app.setApplicationName('Local Arenti Baby Cam')
    app.setOrganizationName('LocalArentiBabyCam')
    app.setApplicationVersion(VERSION)
    app.setWindowIcon(app_icon())
    app.setStyle('Fusion')
    app.setStyleSheet(STYLE)
    if args.make_icon:
        import struct
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        app_icon().pixmap(256).save(buffer, 'PNG')
        raw = bytes(data)
        Path(args.make_icon).write_bytes(struct.pack('<HHH', 0, 1, 1) + struct.pack('<BBBBHHII', 0, 0, 0, 0, 1, 32, len(raw), 22) + raw)
        qInstallMessageHandler(None)
        return 0
    lock = None
    if not (args.self_test or args.preview or args.preview_popout or args.validate_live):
        base = Path(os.environ['LOCALAPPDATA']) / 'LocalArentiBabyCam'
        base.mkdir(parents=True, exist_ok=True)
        lock = QLockFile(str(base / 'viewer.lock'))
        if not lock.tryLock(100):
            if not args.background and not desktop_activation.show_existing():
                QMessageBox.information(None, 'Local Arenti Baby Cam', 'Already running. Open it from the system tray.')
            qInstallMessageHandler(None)
            return 0
    if not (args.self_test or args.preview or args.preview_popout or args.validate_live):
        from camera_setup import configured, show_setup
        if not configured() and (args.background or not show_setup()):
            return 0
    window = ViewerWindow()
    window.quality.setCurrentIndex(window.quality.findData(args.quality))
    utility = bool(args.self_test or args.preview or args.preview_popout or args.validate_live)
    window.autoplay_on_show = not utility
    tray_ready = window.setup_tray() if not utility else False
    if not args.background or not tray_ready or utility:
        window.show()
    if args.self_test or args.preview or args.preview_popout:
        app.processEvents()
        result = {'nativeUi': True, 'version': VERSION, 'idle': window.camera is None,
                  'defaultQuality': window.quality.currentData(),
                  'qualityModes': [window.quality.itemData(i) for i in range(window.quality.count())],
                  'utilityAutoplayDisabled': not window.autoplay_on_show,
                  'volumeSliderCustomTrack': isinstance(window.volume, VolumeSlider),
                  'startsMuted': window.muted, 'directionButtonsDisabled': all(not b.isEnabled() for b in window.direction_buttons.values()),
                  'cloudRequests': 0, 'physicalMoveRequests': 0, 'mediaSaved': False,
                  'windowWidth': window.width(), 'windowHeight': window.height()}
        if args.self_test:
            window.media_devices.audioOutputsChanged.emit()
            window.audio_retry_timer.timeout.emit()
            assert window.audio_sink is None
            assert window.audio_retry_timer.isActive() and window.audio_retry_timer.interval() == 1000
            result.update(defaultAudioOutputMonitoring=True, audioOutputRecoveryPollMilliseconds=1000,
                audioDeviceEventsDoNotStartIdlePlayback=True)
            recovery_check = PreviewRecovery()
            recovery_check.start(10)
            assert not recovery_check.stalled(29, 0) and recovery_check.stalled(30, 0)
            assert recovery_check.failed(30) == 2 and recovery_check.due(32)
            recovery_check.stop()
            assert not recovery_check.due(100)
            result.update(previewAutomaticRecoveryPolicy=True, previewStartupTimeoutSeconds=20,
                previewFrameTimeoutSeconds=12, previewMaximumRecoveriesUntilStable=None,
                previewMaximumRetryDelaySeconds=30,
                previewStopCancelsPendingRecovery=True)
            from owlet_overlay import normalize_widget
            synthetic = normalize_widget({'updated_at': datetime.now().astimezone().isoformat(),
                'oxygen_now': 98, 'heart_rate': 120, 'sleep_state': '8', 'sock_reporting': True})
            window.video.show_frame((bytes([40, 72, 88]) * 160 * 90, 160, 90, 480))
            original = window.video.pixmap.toImage()
            window.video.show_owlet(synthetic)
            result.update(pcLocalClockShownWithFreshDecodedVideo=bool(window.video.clock_text.startswith('PC ')),
                owletSourceAgeShown='updated ' in window.video.owlet_updated_text)
            painted = window.video.grab()
            window.toggle_popout()
            app.processEvents()
            result.update(owletSyntheticOverlayPainted=not painted.isNull(),
                owletSourceImageUnchanged=window.video.pixmap.toImage() == original,
                owletPopoutSharesVideoSurface=window.pip_window is not None and window.video.window() is window.pip_window,
                owletWorkerNotStarted=window.owlet is None, realHealthDataUsed=False)
            window.dock_video(restore_main=False)
            window.video.frame_updated_at = time.monotonic() - 2
            window.video.refresh_indicators()
            result['pcClockHiddenWhenVideoStale'] = not window.video.clock_text
            window.video.clear()
        if args.preview:
            window.grab().save(args.preview, 'PNG')
            result['idleUiPreviewOnly'] = True
        if args.preview_popout:
            window.toggle_popout()
            app.processEvents()
            popup = window.pip_window
            result.update(popoutIdleOnly=window.camera is None, sharedVideoSurface=window.video.window() is popup,
                popoutAlwaysOnTop=bool(popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            if sys.platform == 'win32' and app.platformName() == 'windows':
                import ctypes
                from ctypes import wintypes
                get_style = ctypes.windll.user32.GetWindowLongW
                get_style.argtypes = [wintypes.HWND, ctypes.c_int]
                get_style.restype = ctypes.c_long
                result['nativeTopmostStyle'] = bool(get_style(int(popup.winId()), -20) & 0x00000008)
            popup.grab().save(args.preview_popout, 'PNG')
        if args.report:
            save_report(args.report, result)
        window.close()
        window.deleteLater()
        app.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
        app.processEvents()
        qInstallMessageHandler(None)
        return 0
    if args.validate_live:
        deadline = time.monotonic() + args.validate_live + 20
        first_frame_at = None
        requested_stop = False
        report = {}
        validation = QTimer()
        def check():
            nonlocal first_frame_at, requested_stop, report
            stats = window.mailbox.snapshot() if window.mailbox else window.last_stats
            if stats.get('videoFrames') and first_frame_at is None:
                first_frame_at = time.monotonic()
            enough = first_frame_at is not None and time.monotonic() - first_frame_at >= args.validate_live
            if not requested_stop and (enough or time.monotonic() >= deadline):
                report = dict(stats)
                report.update(nativeUi=True, startsMuted=window.muted, displayedFrame=window.video.pixmap is not None,
                    ptzCapabilitiesRead=bool(window.movement and window.movement.ready),
                    physicalMoveRequests=window.movement.moves if window.movement else 0,
                    mediaSaved=bool(stats.get('mediaSaved')), requestedSeconds=args.validate_live, durationReached=enough)
                requested_stop = True
                window.stop_live()
            if requested_stop and window.camera is None:
                report['workersStopped'] = True
                report['success'] = bool(report.get('displayedFrame') and report.get('audioSamples', 0) > 0
                    and report.get('durationReached') and report.get('physicalMoveRequests') == 0
                    and not report.get('mediaSaved'))
                if args.report:
                    save_report(args.report, report)
                validation.stop()
                window.close()
        validation.timeout.connect(check)
        validation.start(100)
    if args.validate_live:
        # Utility mode bypasses visible-window autoplay and uses this explicit diagnostic.
        QTimer.singleShot(0, window.start_live)
    else:
        window.activation_server = desktop_activation.listen_for_activation(window)
        app.aboutToQuit.connect(window.shutdown_background)
        app.commitDataRequest.connect(lambda _session: window.request_exit())
        QTimer.singleShot(0, window.start_background)
    code = app.exec()
    window.deleteLater()
    app.sendPostedEvents(None, int(QEvent.Type.DeferredDelete))
    app.processEvents()
    qInstallMessageHandler(None)
    return code if not args.validate_live else (0 if report.get('success') else 1)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        # Never display exception text: network errors may embed credentials.
        if QApplication.instance():
            QMessageBox.critical(None, 'Local Arenti Baby Cam', f'The app could not continue ({type(exc).__name__}).')
        raise SystemExit(1)
