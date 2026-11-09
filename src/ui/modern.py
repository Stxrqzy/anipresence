"""Scoped styling for the modern shell; the classic theme remains independent."""


def navigation_icon(index, color):
    """Small vector icons, independent of installed symbol fonts."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QIcon, QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer
    paths = [
        '<path d="M3 10 12 3l9 7v11h-6v-7H9v7H3z"/>',
        '<path d="M3 4v6h6M3 10a9 9 0 1 1 0 5M12 7v5l3 2"/>',
        '<rect x="3" y="4" width="7" height="17" rx="1"/><path d="m14 4 5-1 4 17-5 1zM5 8h3M5 17h3"/>',
        '<path d="M4 20V12h4v8m2 0V7h4v13m2 0V3h4v17M2 21h20"/>',
        '<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3"/><circle cx="15" cy="17" r="3"/>',
        '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7 8 3 3-3 3m6 2h4"/>',
    ]
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{paths[index]}</g></svg>'
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def modern_qss(theme, accent):
    light = theme == "light"
    surface = "#ffffff" if light else "#191c26"
    border = "#dfe3ed" if light else "#2b3040"
    muted = "#657086" if light else "#a4aec3"
    text = "#202638" if light else "#f0f3fb"
    hover = "#edf0f8" if light else "#252b3b"
    return f"""
    QWidget#modernRoot {{ background: transparent; }}
    QWidget#modernRoot QWidget#surfaceContent, QWidget#modernRoot QLabel, QWidget#modernRoot QCheckBox {{ background: transparent; }}
    QWidget#modernRoot QLabel#cover {{ background: {hover}; }}
    QWidget#modernRoot QLabel#avatar {{ background: {hover}; }}
    QWidget#modernRoot QLabel#chipOk {{ background: rgba(74, 222, 128, 0.12); }}
    QWidget#modernRoot QLabel#chipBad {{ background: rgba(248, 113, 113, 0.12); }}
    QWidget#modernRoot QFrame#sidebar {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
    QWidget#modernRoot QPushButton#navButton {{ background: transparent; color: {muted}; border: 0; border-radius: 8px; text-align: left; padding: 12px; font-weight: 600; }}
    QWidget#modernRoot QPushButton#navButton:hover {{ background: {hover}; color: {text}; }}
    QWidget#modernRoot QPushButton#navButton:checked {{ background: {accent}; color: white; }}
    QWidget#modernRoot QLabel#pageTitle {{ font-size: 26px; font-weight: 700; color: {text}; }}
    QWidget#modernRoot QLabel#pageDescription {{ color: {muted}; font-size: 13px; }}
    QWidget#modernRoot QLabel#titleLabel {{ font-size: 25px; font-weight: 700; color: {text}; }}
    QWidget#modernRoot QLabel#sectionLabel {{ color: {muted}; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
    QWidget#modernRoot QFrame#card, QWidget#modernRoot QFrame#controls {{ background: {surface}; border: 1px solid {border}; border-radius: 14px; }}
    QWidget#modernRoot QPushButton#ghost {{ background: transparent; border: 1px solid {border}; border-radius: 8px; padding: 9px 12px; color: {text}; }}
    QWidget#modernRoot QPushButton#ghost:hover {{ background: {hover}; border-color: {accent}; }}
    QWidget#modernRoot QPushButton:disabled {{ color: {muted}; background: transparent; }}
    QWidget#modernRoot QPushButton#ghost:disabled {{ color: {muted}; background: transparent; }}
    QWidget#modernRoot QProgressBar {{ background: {hover}; border: 0; border-radius: 3px; min-height: 6px; max-height: 6px; }}
    QWidget#modernRoot QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
    QWidget#modernRoot QLineEdit, QWidget#modernRoot QComboBox, QWidget#modernRoot QSpinBox {{ border: 1px solid {border}; border-radius: 8px; padding: 8px; }}
    """
