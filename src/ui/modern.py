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


def revamp_qss(theme, accent, glass=True):
    """The v3 reference palette: violet glass, restrained glow, crisp hierarchy."""
    light = theme == "light"
    ink = "#282137" if light else "#f5f1ff"
    muted = "#776a90" if light else "#b5aacd"
    edge = "rgba(107,79,160,55)" if light else "rgba(171,135,232,65)"
    panel = ("rgba(255,255,255,205)" if light else "rgba(26,24,45,210)") if glass else ("#ffffff" if light else "#191728")
    from PySide6.QtGui import QColor
    color = QColor(accent)
    brighter = color.lighter(140).name()
    darker = color.darker(150).name()
    top_edge = f"border-top-color: {'#ffffff' if light else '#61517e'};" if glass else ""
    return f"""
    QWidget#modernRoot {{ color: {ink}; }}
    QWidget#modernRoot QFrame#sidebar {{ background: {'rgba(246,241,255,225)' if light else 'rgba(18,15,33,230)'}; border: 1px solid {edge}; border-radius: 16px; }}
    QWidget#modernRoot QFrame#card, QWidget#modernRoot QFrame#controls,
    QWidget#modernRoot QFrame#preview, QWidget#modernRoot QFrame#continueStrip {{ background: {panel}; border: 1px solid {edge}; {top_edge} border-radius: 14px; }}
    QWidget#modernRoot QLabel#pageTitle {{ color: {ink}; font-size: 34px; font-weight: 750; }}
    QWidget#modernRoot QLabel#pageDescription {{ color: {muted}; font-size: 14px; }}
    QWidget#modernRoot QLabel#cardHeading {{ color: {ink}; font-size: 17px; font-weight: 700; }}
    QWidget#modernRoot QLabel#metricValue {{ color: {ink}; font-size: 29px; font-weight: 750; }}
    QWidget#modernRoot QLabel#sectionLabel {{ color: {muted}; font-size: 12px; letter-spacing: 0.4px; }}
    QWidget#modernRoot QLabel#titleLabel {{ font-size: 30px; }}
    QWidget#modernRoot QLabel#captionLabel {{ color: {brighter}; font-size: 14px; }}
    QWidget#modernRoot QPushButton#navButton {{ padding: 14px 12px; font-size: 14px; border: 1px solid transparent; border-radius: 11px; }}
    QWidget#modernRoot QPushButton#navButton:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {darker},stop:1 {accent}); border: 1px solid {brighter}; color: white; }}
    QWidget#modernRoot QPushButton#profileButton {{ background: {panel}; border: 1px solid {edge}; border-radius: 12px; padding: 14px 10px; text-align: left; color: {ink}; }}
    QWidget#modernRoot QPushButton#primaryAction {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {brighter},stop:1 {accent}); border: 1px solid {brighter}; padding: 10px 18px; border-radius: 10px; color: white; }}
    QWidget#modernRoot QPushButton#ghost {{ border: 1px solid {edge}; padding: 9px 13px; border-radius: 9px; }}
    QWidget#modernRoot QLabel#chipOk {{ border: 1px solid rgba(55,210,125,110); color: {'#167c42' if light else '#67ef94'}; padding: 6px 12px; }}
    QWidget#modernRoot QLabel#chipBad {{ border: 1px solid rgba(245,92,120,100); color: {'#ad304d' if light else '#ff8599'}; padding: 6px 12px; }}
    QWidget#modernRoot QPushButton#winMin, QWidget#modernRoot QPushButton#winMax, QWidget#modernRoot QPushButton#winClose {{ background: transparent; border: none; border-radius: 5px; color: {muted}; font-size: 16px; padding: 0; }}
    QWidget#modernRoot QPushButton#winClose:hover {{ background: #bb3255; color: white; }}
    QWidget#modernRoot QPushButton#winMin:hover, QWidget#modernRoot QPushButton#winMax:hover {{ background: {panel}; color: {ink}; }}
    QWidget#modernRoot QTabBar::tab {{ background: {panel}; border: 1px solid {edge}; border-radius: 10px; padding: 9px 16px; color: {muted}; }}
    QWidget#modernRoot QTabBar::tab:selected {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 {accent},stop:1 {darker}); border-color: {brighter}; color: white; }}
    QWidget#modernRoot QLineEdit, QWidget#modernRoot QComboBox {{ background: {'rgba(255,255,255,185)' if light else 'rgba(13,12,25,170)'}; border: 1px solid {edge}; padding: 9px 12px; border-radius: 9px; color: {ink}; }}
    QWidget#modernRoot QListWidget#animeList {{ background: {'rgba(255,255,255,130)' if light else 'rgba(13,12,25,110)'}; border: 1px solid {edge}; padding: 6px; }}
    QWidget#modernRoot QListWidget#animeList::item:selected {{ background: {'#e1d4fa' if light else '#34264f'}; border: none; }}
    QWidget#modernRoot QTableWidget {{ background: {panel}; alternate-background-color: {'#f0eafa' if light else '#201b32'}; color: {ink}; border: none; outline: none; selection-background-color: {darker}; selection-color: white; }}
    QWidget#modernRoot QTableWidget::item {{ padding: 8px; border: none; }}
    QWidget#modernRoot QHeaderView::section {{ background: {'#ece5f7' if light else '#252039'}; color: {muted}; border: none; padding: 12px 8px; font-weight: 600; }}
    QWidget#modernRoot QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {accent},stop:1 {brighter}); }}
    """
