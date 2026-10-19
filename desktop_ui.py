"""Qt 原生桌面界面。所有业务结果均从服务队列进入主线程。"""
import copy
import json
from pathlib import Path
import queue
import time
import uuid
from PySide6.QtCore import Qt, QTimer, QSize, QSortFilterProxyModel, QRectF, QPointF
from PySide6.QtGui import QColor, QFont, QIcon, QKeySequence, QPainter, QPalette, QPen, QPixmap, QStandardItem, QStandardItemModel, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QAbstractSpinBox, QApplication, QButtonGroup, QCheckBox, QComboBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox,
    QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QStackedWidget,
    QTableView, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)
from mode_config import selected_spec, validate_target, WEBSITES
from ui_state import RuntimeState
from lobby_flow import LobbyStatus


STYLE = '''
* { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: 13px; color: #273b43; }
QMainWindow, QDialog { background: #f5f7f8; }
QWidget#page { background: #f5f7f8; }
QLabel { background: transparent; }
QLabel#brand { font-size: 19px; font-weight: 700; color: #116e68; }
QLabel#title { font-size: 24px; font-weight: 700; letter-spacing: 1px; }
QLabel#heading { font-size: 17px; font-weight: 700; }
QLabel#hero { font-size: 27px; font-weight: 700; color: #176e68; }
QLabel#muted { color: #617680; font-size: 12px; }
QLabel#eyebrow { color: #668078; font-size: 12px; font-weight: 600; }
QLabel#notice { background: #edf5f4; color: #226d65; border: 1px solid #d3e7e3; border-radius: 8px; padding: 11px; }
QLabel#warning { background: #fff6e9; color: #8d591b; border: 1px solid #f0dfc4; border-radius: 8px; padding: 11px; }
QFrame#panel { background: white; border: 1px solid #e1e8eb; border-radius: 12px; }
QFrame#heroPanel { background: white; border: 1px solid #e1e8eb; border-top: 3px solid #187d73; border-radius: 12px; }
QFrame#diagnostics { background: #edf3f4; border-radius: 9px; }
QFrame#statusbar { background: white; border-bottom: 1px solid #e4e9eb; }
QFrame#navigationBar { background: white; border-bottom: 1px solid #e4e9eb; }
QLabel#flowStep { background: #f0f4f5; color: #71818a; border-radius: 5px; padding: 7px 8px; font-size: 12px; }
QLabel#flowStep[active="true"] { background: #dcefe9; color: #116b60; font-weight: 700; }
QLabel#targetMark { background: #e7f3ef; color: #187d73; border-radius: 10px; font-size: 22px; font-weight: 700; }
QPushButton { background: white; border: 1px solid #d6e0e3; border-radius: 7px; padding: 8px 14px; min-height: 18px; }
QPushButton:hover { background: #edf5f4; border-color: #9fc9c3; }
QPushButton:pressed { background: #dcece9; }
QPushButton:disabled { color: #9ba6ad; background: #f2f4f5; border-color: #e1e7e9; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QTableView:focus { border: 2px solid #29978e; }
QPushButton#primary { background: #187d73; color: white; border-color: #187d73; font-weight: 600; }
QPushButton#primary:hover { background: #126b63; }
QPushButton#primary:disabled { background: #b5ccc8; border-color: #b5ccc8; }
QPushButton#segment { background: #edf1f2; border: 1px solid transparent; padding: 9px 22px; }
QPushButton#segment:checked { background: white; color: #14766e; border-color: #aacfc9; font-weight: 700; }
QPushButton#site { text-align: left; padding: 20px; background: white; border-radius: 10px; min-height: 66px; }
QPushButton#site:checked { border: 2px solid #20867b; background: #f0f8f6; }
QListWidget#navigation { background: transparent; border: 0; outline: 0; }
QListWidget#navigation::item { padding: 7px 10px; margin: 3px 4px; border-bottom: 2px solid transparent; color: #60727a; }
QListWidget#navigation::item:selected { background: #f0f7f4; color: #136f66; border-bottom: 2px solid #187d73; font-weight: 700; }
QListWidget#navigation::item:hover { background: #f1f6f5; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox { background: white; border: 1px solid #d7e1e5; border-radius: 6px; padding: 7px 9px; min-height: 20px; selection-background-color: #d3ebe6; }
QComboBox { padding-right: 25px; }
QComboBox::drop-down { border: 0; width: 24px; }
QCheckBox { spacing: 9px; padding: 4px 0; }
QCheckBox::indicator { width: 17px; height: 17px; }
QTableView, QListWidget#results { background: white; alternate-background-color: #f8fafb; border: 1px solid #e1e8eb; border-radius: 8px; gridline-color: #edf1f3; selection-background-color: #dcefeb; selection-color: #155e57; }
QHeaderView::section { background: #f5f8f9; color: #62757e; border: 0; border-bottom: 1px solid #e1e8eb; padding: 10px 8px; text-align: left; }
QTableView::item { padding: 7px; border: 0; }
QTabWidget::pane { background: transparent; border: 0; padding-top: 15px; }
QTabBar::tab { background: transparent; border-bottom: 2px solid transparent; padding: 12px 16px; color: #788990; }
QTabBar::tab:selected { color: #13786e; border-bottom: 2px solid #187d73; font-weight: 700; }
QGroupBox { border: 1px solid #e1e8eb; border-radius: 8px; margin-top: 15px; padding: 14px; background: white; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 5px; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #cfdbdf; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #263c43; color: white; border: 0; padding: 6px; }
'''
STYLE += 'QComboBox::down-arrow { image: url("' + (Path(__file__).resolve().parent/'assets/chevron-down.svg').as_posix() + '"); width: 14px; height: 14px; }'
STYLE += '''
QDoubleSpinBox, QSpinBox { padding-right: 28px; }
QDoubleSpinBox::up-button, QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 24px; border: 0; }
QDoubleSpinBox::down-button, QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 24px; border: 0; }
'''
STYLE += 'QDoubleSpinBox::up-arrow, QSpinBox::up-arrow { image: url("' + (Path(__file__).resolve().parent/'assets/chevron-up.svg').as_posix() + '"); width: 10px; height: 10px; }'
STYLE += 'QDoubleSpinBox::down-arrow, QSpinBox::down-arrow { image: url("' + (Path(__file__).resolve().parent/'assets/chevron-down.svg').as_posix() + '"); width: 10px; height: 10px; }'


def label(text='', kind=None, wrap=True):
    widget = QLabel(text)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if kind:
        widget.setObjectName(kind)
    return widget


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(callback)
    return widget


def panel():
    widget = QFrame()
    widget.setObjectName('panel')
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(22, 20, 22, 20)
    layout.setSpacing(13)
    return widget, layout


def hrow(*items):
    layout = QHBoxLayout()
    layout.setSpacing(10)
    for item in items:
        if item is None:
            layout.addStretch()
        elif isinstance(item, QWidget):
            layout.addWidget(item)
        else:
            layout.addLayout(item)
    return layout


def line_icon(index):
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#547a75'), 1.6))
    if index == 0:
        painter.drawRoundedRect(4, 4, 16, 16, 3, 3)
        painter.drawLine(7, 15, 10, 11); painter.drawLine(10, 11, 13, 13); painter.drawLine(13, 13, 17, 8)
    elif index == 1:
        painter.drawRoundedRect(4, 4, 16, 16, 2, 2); painter.drawLine(9, 4, 9, 20)
        painter.drawLine(12, 9, 17, 9); painter.drawLine(12, 13, 17, 13)
    elif index == 2:
        painter.drawRoundedRect(3, 5, 18, 14, 3, 3)
        painter.drawLine(10, 9, 15, 12); painter.drawLine(15, 12, 10, 15); painter.drawLine(10, 15, 10, 9)
    elif index == 3:
        for y, x in [(6, 9), (12, 16), (18, 8)]:
            painter.drawLine(3, y, 21, y); painter.drawEllipse(x-2, y-2, 4, 4)
    elif index == 5:
        painter.drawRoundedRect(3, 6, 18, 13, 3, 3)
        painter.drawLine(6, 12, 12, 12); painter.drawLine(9, 9, 9, 15)
        painter.drawEllipse(15, 10, 2, 2); painter.drawEllipse(17, 13, 2, 2)
    else:
        painter.drawRoundedRect(5, 3, 14, 18, 2, 2)
        for y in [8, 12, 16]: painter.drawLine(8, y, 16, y)
    painter.end()
    return QIcon(pixmap)


class LogFilter(QSortFilterProxyModel):
    category = '全部类别'
    query = ''
    def filterAcceptsRow(self, row, parent):
        model = self.sourceModel()
        texts = [model.index(row, col, parent).data() or '' for col in range(3)]
        return (self.category == '全部类别' or self.category == texts[1]) and self.query.casefold() in ' '.join(texts).casefold()


class TargetEditor(QDialog):
    def __init__(self, parent, preset=None):
        super().__init__(parent)
        self.preset = copy.deepcopy(preset or {})
        self.result_spec = None
        self.setWindowTitle('配置桌面学习软件')
        self.resize(640, 450)
        self.setMinimumWidth(540)
        container = QVBoxLayout(self)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); scroll.setWidget(content); container.addWidget(scroll)
        outer = QVBoxLayout(content); outer.setSpacing(14)
        outer.addWidget(label('添加你已安装的学习软件', 'heading'))
        outer.addWidget(label('优先选择程序文件；有启动器时，可在高级配置中指定实际窗口所属进程。', 'muted'))
        form = QFormLayout(); form.setVerticalSpacing(12)
        self.name = QLineEdit(self.preset.get('name', ''))
        self.path = QLineEdit((self.preset.get('command') or [''])[0])
        self.path.setPlaceholderText('选择 .exe 文件，或填写完整启动路径')
        form.addRow('软件名称', self.name)
        form.addRow('启动程序', hrow(self.path, button('选择文件…', self.browse)))
        self.pin = QCheckBox('允许此目标在死亡期间持续置顶'); self.pin.setChecked(self.preset.get('pin', True))
        form.addRow('', self.pin); outer.addLayout(form)
        self.advanced_toggle = QCheckBox('高级匹配条件与启动参数')
        self.advanced = QWidget(); advanced = QFormLayout(self.advanced); advanced.setContentsMargins(0, 0, 0, 0)
        self.process = QLineEdit(self.preset.get('process_path', ''))
        self.title_match = QLineEdit(self.preset.get('title_contains', ''))
        self.class_match = QLineEdit(self.preset.get('window_class', ''))
        self.arguments = QLineEdit(json.dumps(self.preset.get('command', [])[1:], ensure_ascii=False))
        for text, widget in [('完整进程路径', self.process), ('窗口标题包含', self.title_match), ('窗口类名（完全匹配）', self.class_match), ('额外启动参数（数组）', self.arguments)]: advanced.addRow(text, widget)
        self.advanced.setVisible(False); self.advanced_toggle.toggled.connect(self.advanced.setVisible)
        outer.addWidget(self.advanced_toggle); outer.addWidget(self.advanced)
        outer.addWidget(label('多个窗口无法唯一识别时会停止操作。全局置顶与本目标置顶同时开启才维持层级。', 'muted'))
        self.error = label('', 'warning'); self.error.hide(); outer.addWidget(self.error)
        outer.addStretch()
        outer.addLayout(hrow(None, button('取消', self.reject), button('保存并设为学习目标', self.save, True)))

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择学习软件', '', 'Windows 桌面程序 (*.exe)')
        if path:
            self.path.setText(path)
            self.process.setText(path)
            if not self.name.text(): self.name.setText(Path(path).stem)

    def save(self):
        try:
            arguments = json.loads(self.arguments.text() or '[]')
            if not isinstance(arguments, list) or any(not isinstance(v, str) for v in arguments):
                raise ValueError('额外参数需为字符串数组，例如 ["--profile", "学习"]')
            spec = dict(self.preset, id=self.preset.get('id') or 'desktop-' + uuid.uuid4().hex[:12],
                adapter=self.preset.get('adapter', 'desktop'), name=self.name.text().strip(),
                command=[self.path.text().strip(), *arguments], process_path=self.process.text().strip() or self.path.text().strip(),
                title_contains=self.title_match.text().strip(), window_class=self.class_match.text().strip(), pin=self.pin.isChecked())
            validate_target(spec)
            if not Path(spec['command'][0]).is_file(): raise ValueError('启动文件不存在，请选择本机真实程序。')
            self.result_spec = spec
            self.accept()
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc)); self.error.show()


class DownloadPrompt(QDialog):
    """仅在后端请求时创建；取消、复活、暂停均立即拒绝下载。"""
    def __init__(self, parent, service, request):
        super().__init__(parent)
        self.service, self.request = service, request
        self.finished_request = False
        self.setWindowTitle('官网下载确认')
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.resize(520, 260)
        layout = QVBoxLayout(self); layout.setContentsMargins(24, 22, 24, 22); layout.setSpacing(16)
        layout.addWidget(label('是否下载原神 Windows 安装程序？', 'heading'))
        layout.addWidget(label('原神官网已打开。确认后将通过官网入口发起 Windows 客户端安装程序或启动器的浏览器下载；这不表示已下载完整游戏。不会自动运行或安装。'))
        layout.addWidget(label('选择“仅保留官网”或关闭此窗口即可取消。复活、暂停或退出后，本次下载请求也会取消。', 'muted'))
        self.approve_button = button('确认下载', lambda: self.finish(True), True)
        layout.addLayout(hrow(button('仅保留官网', lambda: self.finish(False)), None, self.approve_button))
        self.timer = QTimer(self); self.timer.timeout.connect(self.watch); self.timer.start(80)

    def watch(self):
        reply, answer, cancelled = self.request
        if cancelled.is_set() or self.service.closing or self.service.stop_event.is_set(): self.finish(False)

    def finish(self, approved=False):
        if self.finished_request: return
        reply, answer, cancelled = self.request
        answer['approved'] = bool(approved and not cancelled.is_set() and not self.service.closing and not self.service.stop_event.is_set())
        self.finished_request = True; reply.set(); self.timer.stop(); super().accept()

    def reject(self): self.finish(False)
    def closeEvent(self, event): self.finish(False); event.accept()


class AvatarCanvas(QWidget):
    def __init__(self, frame):
        super().__init__();self.image=frame.image();self.start=None;self.selection=None
        self.setMinimumSize(460,280);self.setMouseTracking(True)

    def mapping(self):
        scale=min(self.width()/self.image.width(),self.height()/self.image.height())
        return scale,(self.width()-self.image.width()*scale)/2,(self.height()-self.image.height()*scale)/2

    def point(self, position):
        scale,x,y=self.mapping()
        return QPointF(max(0,min(self.image.width(),(position.x()-x)/scale)),max(0,min(self.image.height(),(position.y()-y)/scale)))

    def paintEvent(self, event):
        painter=QPainter(self);scale,x,y=self.mapping()
        painter.drawImage(QRectF(x,y,self.image.width()*scale,self.image.height()*scale),self.image)
        if self.selection:
            painter.setPen(QPen(QColor('#29b49a'),2))
            rect=self.selection
            painter.drawRect(QRectF(x+rect.x()*scale,y+rect.y()*scale,rect.width()*scale,rect.height()*scale))
        painter.end()

    def mousePressEvent(self, event):
        if event.button()==Qt.MouseButton.LeftButton:self.start=self.point(event.position());self.selection=None

    def mouseMoveEvent(self, event):
        if self.start is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.selection=QRectF(self.start,self.point(event.position())).normalized();self.update()

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            self.selection=QRectF(self.start,self.point(event.position())).normalized();self.start=None;self.update()


class AvatarDialog(QDialog):
    def __init__(self, parent, service, frame):
        super().__init__(parent);self.service,self.frame=service,frame
        self.setWindowTitle('校准右上角头像');self.resize(940,620)
        layout=QVBoxLayout(self)
        layout.addWidget(label('框选右上角圆形头像的内部图案', 'heading'))
        layout.addWidget(label('只保存选中头像的小图与相对位置，完整客户端画面不会保存或上传。换头像后需要重新校准。', 'muted'))
        self.canvas=AvatarCanvas(frame);layout.addWidget(self.canvas,1)
        self.error=label('', 'warning');self.error.hide();layout.addWidget(self.error)
        layout.addLayout(hrow(button('取消',self.reject),None,button('保存头像定位',self.save,True)))

    def save(self):
        rect=self.canvas.selection
        if rect is None:self.error.setText('请先拖动框选右上角头像。');self.error.show();return
        try:
            self.service.lobby.surface.save_avatar(self.frame,[rect.left(),rect.top(),rect.right(),rect.bottom()])
            self.accept()
        except (ValueError,OSError) as exc:self.error.setText(str(exc));self.error.show()


class MainWindow(QMainWindow):
    def __init__(self, service, autostart=True):
        super().__init__()
        app = QApplication.instance()
        if app.style().objectName().casefold() != 'fusion': app.setStyle('Fusion')
        palette = app.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor('#187d73'))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor('white'))
        app.setPalette(palette)
        self.service = service
        self.state = RuntimeState()
        self.closing = False
        self.hotkey_down = False
        self.detected, self.visible_apps, self.site_items = [], [], []
        self.search_name, self.search_kind = '', 'catalog'
        self.last_inspection = 0
        self.pending_open = False
        self.prompts = []
        self.setWindowTitle('泉水时间管理 · LOL Words')
        self.setWindowIcon(line_icon(0))
        self.resize(1120, 780); self.setMinimumSize(880, 640)
        screen = QApplication.primaryScreen()
        if screen:
            self.adapt_to_area(screen.availableGeometry())
        self.setStyleSheet(STYLE)
        self.build()
        self.refresh_config()
        self.load_recent_log()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(80)
        self.append_log('界面已就绪；死亡判断与窗口切换由现有后端负责。', persist=False)
        if service.config_error:
            self.show_notice(service.config_error, True)
        elif autostart:
            QTimer.singleShot(300, service.start)

    def adapt_to_area(self, area):
        """高缩放下按工作区约束初始尺寸，页面仍可滚动访问。"""
        width, height = max(600, area.width()-32), max(320, area.height()-56)
        self.setMinimumSize(min(880, width), min(640, height))
        self.resize(min(self.width(), width), min(self.height(), height))

    def build(self):
        base = QWidget(); self.setCentralWidget(base)
        body = QVBoxLayout(base); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        bar = QFrame(); bar.setObjectName('statusbar'); bl = QHBoxLayout(bar); bl.setContentsMargins(28, 16, 28, 16); bl.setSpacing(18)
        identity = QVBoxLayout(); identity.setSpacing(1)
        identity.addWidget(label('泉水时间管理', 'brand'))
        identity.addWidget(label('LOL WORDS  /  游戏伴侣', 'muted', wrap=False))
        bl.addLayout(identity)
        self.bar_target = label('正在读取配置', 'muted'); self.bar_target.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.monitor_badge = label('●  已暂停', wrap=False)
        self.pause_button = button('继续监测', self.toggle_pause, True); self.pause_button.setMinimumWidth(110)
        bl.addWidget(self.bar_target, 1); bl.addWidget(self.monitor_badge); bl.addWidget(self.pause_button); body.addWidget(bar)
        navigation_bar = QFrame(); navigation_bar.setObjectName('navigationBar')
        nav_layout = QHBoxLayout(navigation_bar); nav_layout.setContentsMargins(20, 0, 20, 0)
        self.navigation = QListWidget(); self.navigation.setObjectName('navigation'); self.navigation.setIconSize(QSize(20, 20))
        self.navigation.setFlow(QListView.Flow.LeftToRight); self.navigation.setWrapping(False)
        self.navigation.setResizeMode(QListView.ResizeMode.Adjust); self.navigation.setFixedHeight(50)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        names = ['运行首页', '学习目标', '娱乐目标', '通用设置', '运行日志', '大厅助手']
        for index, name in enumerate(names):
            item = QListWidgetItem(line_icon(index), name); item.setSizeHint(QSize(142, 44)); self.navigation.addItem(item)
        self.navigation.setAccessibleName('主导航'); nav_layout.addWidget(self.navigation); body.addWidget(navigation_bar)
        self.stack = QStackedWidget(); body.addWidget(self.stack, 1)
        self.home_page(); self.learning_page(); self.entertainment_page(); self.settings_page(); self.logs_page(); self.lobby_page()
        self.navigation.currentRowChanged.connect(self.stack.setCurrentIndex); self.navigation.setCurrentRow(0)
        footer = QHBoxLayout(); footer.setContentsMargins(28, 5, 28, 9)
        footer.addWidget(label('活着认真操作，复活及时归队。', 'muted', wrap=False)); footer.addStretch()
        footer.addWidget(label('Ctrl + Alt + F8 暂停  ·  Alt + 1–6 导航', 'muted', wrap=False)); body.addLayout(footer)
        for i in range(6):
            sc = QShortcut(QKeySequence(f'Alt+{i+1}'), self)
            sc.activated.connect(lambda i=i: self.navigation.setCurrentRow(i))
        self.copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.log_table)
        self.copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self.copy_log)

    def page(self, title, subtitle, scroll=True):
        widget = QWidget(); widget.setObjectName('page')
        layout = QVBoxLayout(widget); layout.setContentsMargins(28, 22, 28, 20); layout.setSpacing(15)
        if title: layout.addWidget(label(title, 'title'))
        if subtitle: layout.addWidget(label(subtitle, 'muted'))
        if scroll:
            area = QScrollArea(); area.setWidgetResizable(True); area.setWidget(widget); self.stack.addWidget(area)
        else: self.stack.addWidget(widget)
        return widget, layout

    def home_page(self):
        self.home_widget, layout = self.page('', '')
        self.mode_buttons = QButtonGroup(self); self.mode_buttons.setExclusive(True)
        row = QHBoxLayout(); row.setSpacing(3)
        row.addWidget(label('把等待时间，留给自己。', 'muted', wrap=False)); row.addStretch()
        for mode, text in [('study', '学习模式'), ('entertainment', '娱乐模式')]:
            b = QPushButton(text); b.setObjectName('segment'); b.setCheckable(True); b.setProperty('mode', mode)
            b.clicked.connect(lambda checked, mode=mode: self.set_mode(mode)); self.mode_buttons.addButton(b); row.addWidget(b)
        layout.addLayout(row)
        self.notice = label('', 'notice'); self.notice.hide(); layout.addWidget(self.notice)
        self.home_grid = QGridLayout(); self.home_grid.setSpacing(16); layout.addLayout(self.home_grid)
        self.hero_panel, inner = panel(); self.hero_panel.setObjectName('heroPanel')
        self.game_label = label('尚未读取', 'eyebrow'); inner.addWidget(self.game_label)
        self.flow_label = label('等待监测', 'hero'); inner.addWidget(self.flow_label)
        self.reason_label = label('继续后等待游戏。'); inner.addWidget(self.reason_label)
        self.respawn_label = label('', 'heading'); self.respawn_label.hide(); inner.addWidget(self.respawn_label)
        inner.addStretch()
        stages = QHBoxLayout(); stages.setSpacing(6); self.flow_steps = []
        for text in ['确认死亡', '切出延迟', '画面确认', '显示目标', '复活返回']:
            step = label(text, 'flowStep'); step.setAlignment(Qt.AlignmentFlag.AlignCenter); step.setProperty('active', False)
            self.flow_steps.append(step); stages.addWidget(step, 1)
        inner.addLayout(stages)
        self.target_panel, target = panel(); self.target_panel.setMinimumWidth(250); target.setSpacing(8); target.setContentsMargins(18, 16, 18, 16)
        self.target_mark = label('学', 'targetMark'); self.target_mark.setFixedSize(36, 36); self.target_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        target.addLayout(hrow(self.target_mark, label('当前目标', 'eyebrow'), None))
        self.target_label = label('', 'heading'); self.target_status = label('', 'muted')
        target.addWidget(self.target_label); target.addWidget(self.target_status)
        self.target_hint = label('', 'warning'); self.target_hint.hide(); target.addWidget(self.target_hint)
        self.open_button = button('打开 / 重新打开目标', self.open_current, True)
        self.configure_button = button('管理当前目标', self.manage_current)
        target.addWidget(self.open_button); target.addWidget(self.configure_button)
        target.addWidget(label('首次需要时启动，后续复用。\n对局结束保留窗口。', 'muted')); target.addStretch()
        self._home_stacked = None
        self.update_home_layout()
        diagnostics = QFrame(); diagnostics.setObjectName('diagnostics'); details = QHBoxLayout(diagnostics); details.setContentsMargins(18, 13, 18, 13); details.setSpacing(24)
        self.switch_label, self.pin_label, self.rules_label = label(), label(), label()
        for title, value in [('窗口切换', self.switch_label), ('临时置顶', self.pin_label), ('本次切换规则', self.rules_label)]:
            column = QVBoxLayout(); column.setSpacing(4); column.addWidget(label(title, 'muted')); column.addWidget(value); details.addLayout(column, 1)
        layout.addWidget(diagnostics)
        recent_header = hrow(label('最近事件', 'heading'), None, button('查看全部', lambda: self.navigation.setCurrentRow(4)))
        layout.addLayout(recent_header)
        self.recent_label = label('尚无运行事件。', 'muted'); self.recent_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); layout.addWidget(self.recent_label)
        self.lobby_summary=label('大厅助手：三个功能均未开启。','muted',wrap=False)
        layout.addLayout(hrow(self.lobby_summary,None,button('大厅助手',lambda:self.navigation.setCurrentRow(5))))
        layout.addStretch()

    def update_home_layout(self):
        """窄工作区把目标面板放到流程下方，不压缩或隐藏状态。"""
        stacked = self.width() < 820
        if stacked == self._home_stacked: return
        self.home_grid.removeWidget(self.hero_panel); self.home_grid.removeWidget(self.target_panel)
        self.home_grid.addWidget(self.hero_panel, 0, 0)
        self.home_grid.addWidget(self.target_panel, 1 if stacked else 0, 0 if stacked else 1)
        self.home_grid.setColumnStretch(0, 1 if stacked else 3)
        self.home_grid.setColumnStretch(1, 0 if stacked else 1)
        self._home_stacked = stacked

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'home_grid'): self.update_home_layout()
        if hasattr(self, 'navigation'):
            size = max(80, min(150, (self.width()-56)//self.navigation.count()))
            self.navigation.setIconSize(QSize(18,18) if self.width()>=900 else QSize(0,0))
            for index in range(self.navigation.count()): self.navigation.item(index).setSizeHint(QSize(size, 44))

    def refresh_rules(self):
        runtime = self.service.runtime
        config = runtime.config if self.service.running and runtime is not None else self.service.config
        self.rules_label.setText(f"死亡后 {config['death_switch_delay']:g} 秒\n复活前 {config['return_before_seconds']:g} 秒")

    def learning_page(self):
        _, layout = self.page('学习目标', '自动发现、按名称找官网，或接入你自己的桌面软件。')
        self.target_combo = QComboBox(); self.target_combo.setMinimumWidth(190)
        self.target_combo.activated.connect(self.choose_saved)
        self.edit_target_button = button('编辑目标', self.edit_current)
        layout.addLayout(hrow(label('当前学习目标'), self.target_combo, self.edit_target_button, None))
        self.tabs = QTabWidget(); layout.addWidget(self.tabs)
        installed = QWidget(); il = QVBoxLayout(installed); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(14); self.tabs.addTab(installed, '已安装的软件')
        self.scan_button = button('检测学习软件', self.scan, True)
        self.app_filter = QLineEdit(); self.app_filter.setPlaceholderText('按软件名称筛选'); self.app_filter.textChanged.connect(self.render_apps)
        il.addLayout(hrow(self.app_filter, self.scan_button))
        self.scan_status = label('尚未检测。软件库中的收录条目不代表本机已安装。', 'notice'); il.addWidget(self.scan_status)
        self.missing = QCheckBox('同时显示软件库中未检测到的项目'); self.missing.toggled.connect(self.render_apps); il.addWidget(self.missing)
        self.app_table = QTableWidget(0, 4); self.app_table.setHorizontalHeaderLabels(['软件', '本机状态', '识别来源', '实际位置'])
        self.setup_table(self.app_table); self.app_table.setMinimumHeight(210); il.addWidget(self.app_table)
        self.app_table.itemSelectionChanged.connect(self.update_app_actions)
        self.app_table.doubleClicked.connect(lambda _: self.select_app())
        self.use_app_button = button('设为学习目标', self.select_app, True); self.use_app_button.setEnabled(False)
        il.addLayout(hrow(label('仅检测实际程序，不自动安装。', 'muted'), None, self.use_app_button))
        sites = QWidget(); sl = QVBoxLayout(sites); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(13); self.tabs.addTab(sites, '按名称找官网')
        self.site_query = QLineEdit(); self.site_query.setPlaceholderText('输入官方名称或别名，例如：不背单词、Anki')
        self.search_button = button('查找官网', self.search, True); self.site_query.returnPressed.connect(self.search)
        sl.addLayout(hrow(self.site_query, self.search_button))
        self.search_status = label('内置目录可离线查询；目录外名称将联网获取待确认候选。', 'notice'); sl.addWidget(self.search_status)
        self.site_list = QListWidget(); self.site_list.setObjectName('results'); self.site_list.setMinimumHeight(180); self.site_list.currentRowChanged.connect(self.site_detail); sl.addWidget(self.site_list)
        self.site_notes = label('请选择条目查看实际支持方式。', 'muted'); self.site_notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); sl.addWidget(self.site_notes)
        self.use_site_button = button('设为目标并打开官网', self.select_site, True); self.use_site_button.setEnabled(False)
        sl.addLayout(hrow(label('搜索候选需确认。', 'muted', wrap=False), None, self.use_site_button))
        custom = QWidget(); cl = QVBoxLayout(custom); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(18); self.tabs.addTab(custom, '自定义桌面软件')
        frame, inner = panel(); inner.addWidget(label('把常用学习软件接进来', 'heading'))
        inner.addWidget(label('选择已安装的 .exe，填写名称即可添加。窗口匹配使用完整进程路径；有多个窗口时，请补充标题或窗口类条件。'))
        inner.addWidget(label('启动器、实际进程、启动参数和目标置顶均可独立配置。不会读取学习内容。', 'muted'))
        inner.addLayout(hrow(button('选择程序并添加…', self.add_custom, True), None)); cl.addWidget(frame)
        self.custom_summary = label('', 'muted'); cl.addWidget(self.custom_summary); cl.addStretch()
        self.render_sites(self.service.catalog.search(), 'catalog')
        layout.addStretch()

    def entertainment_page(self):
        _, layout = self.page('娱乐目标', '泉水里的短暂放松，也要记得准时回岗。')
        self.site_buttons = QButtonGroup(self); self.site_buttons.setExclusive(True)
        for key, title in [('bilibili', '哔哩哔哩官网\nwww.bilibili.com'), ('douyin', '抖音官网\nwww.douyin.com')]:
            b = QPushButton(title); b.setObjectName('site'); b.setCheckable(True); b.setProperty('site', key)
            b.clicked.connect(lambda checked, key=key: self.save_change({'entertainment_site': key}))
            self.site_buttons.addButton(b); layout.addWidget(b)
        frame, inner = panel(); layout.addWidget(frame)
        inner.addWidget(label('一个独立窗口，反复使用', 'heading'))
        inner.addWidget(label('首次安全切出时打开本工具专用的浏览器窗口，后续死亡复用同一页面。对局结束保留页面；手动关闭后不会循环拉起。'))
        inner.addWidget(label('需要已安装 Microsoft Edge 或 Google Chrome。登录、验证码、自动播放限制由网站决定；可在专用窗口内自行处理。工具不读取日常浏览器的账号、凭据或浏览记录。', 'muted'))
        self.entertainment_open = button('打开所选网站', self.open_entertainment, True)
        inner.addLayout(hrow(self.entertainment_open, button('切换到娱乐模式', lambda: self.set_mode('entertainment')), None))
        layout.addStretch()

    def settings_page(self):
        _, layout = self.page('通用设置', '给操作留时间，也为准时归队留余量。')
        frame, inner = panel(); layout.addWidget(frame)
        form = QFormLayout(); form.setVerticalSpacing(17); form.setHorizontalSpacing(24)
        self.delay = QDoubleSpinBox(); self.delay.setRange(0, 120); self.delay.setDecimals(2); self.delay.setSingleStep(.5); self.delay.setSuffix(' 秒'); self.delay.setAccessibleName('死亡后切出延迟')
        self.lead = QDoubleSpinBox(); self.lead.setRange(0, 30); self.lead.setDecimals(2); self.lead.setSingleStep(.5); self.lead.setSuffix(' 秒'); self.lead.setAccessibleName('复活前提前返回')
        self.display = QComboBox()
        for name, value in [('无边框', 'borderless'), ('全屏', 'fullscreen'), ('自动兼容', 'auto')]: self.display.addItem(name, value)
        form.addRow('死亡后切出延迟', self.setting_field(self.delay, '从确认本人死亡开始计时；还需等可操作阶段结束。默认 3 秒。'))
        form.addRow('复活前提前返回', self.setting_field(self.lead, '在实际复活倒计时到达此值时返回。默认 1 秒。'))
        form.addRow('游戏显示模式', self.setting_field(self.display, '与游戏内设置一致；自动使用兼容路径。'))
        inner.addLayout(form)
        self.pin = QCheckBox('死亡学习 / 娱乐期间持续置顶'); inner.addWidget(self.pin)
        inner.addWidget(label('全局置顶与目标单独置顶同时开启才维持层级。维持置顶不会持续抢键盘焦点。', 'muted'))
        self.advanced_toggle = QCheckBox('展开高级设置'); inner.addWidget(self.advanced_toggle)
        self.advanced_settings = QWidget(); advanced = QFormLayout(self.advanced_settings); advanced.setContentsMargins(0, 0, 0, 0); advanced.setVerticalSpacing(12)
        self.threshold = QDoubleSpinBox(); self.threshold.setRange(50, 99); self.threshold.setDecimals(1); self.threshold.setSuffix(' %')
        self.frames = QSpinBox(); self.frames.setRange(1, 20)
        self.poll = QDoubleSpinBox(); self.poll.setRange(.05, 5); self.poll.setDecimals(2); self.poll.setSingleStep(.05); self.poll.setSuffix(' 秒')
        self.player = QLineEdit(); self.player.setPlaceholderText('通常留空，使用接口本人身份；仅匹配失败时填写完整标识')
        for field in [self.delay, self.lead, self.threshold, self.frames, self.poll]:
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.UpDownArrows)
            field.setToolTip('可直接输入；使用 ↑ / ↓ 方向键微调。')
        for text, field in [('灰屏占比门槛', self.threshold), ('连续有效确认帧数', self.frames), ('对局轮询间隔', self.poll), ('本人完整标识（可选）', self.player)]: advanced.addRow(text, field)
        inner.addWidget(self.advanced_settings); self.advanced_settings.hide(); self.advanced_toggle.toggled.connect(self.advanced_settings.setVisible)
        self.save_settings_button = button('保存设置', self.save_settings, True)
        inner.addLayout(hrow(self.save_settings_button, None))
        self.settings_notice = label('模式、目标和显示方式即时生效；计时、灰屏判断、本人标识与全局置顶在暂停后继续生效。', 'notice'); layout.addWidget(self.settings_notice)
        self.dirty_settings = False
        self.edited_settings = set()
        for field, key in [(self.delay, 'death_switch_delay'), (self.lead, 'return_before_seconds'),
                           (self.threshold, 'gray_threshold'), (self.frames, 'confirmation_frames'), (self.poll, 'poll_seconds')]:
            field.valueChanged.connect(lambda value, key=key: self.settings_edited(key))
        self.display.currentIndexChanged.connect(lambda _: self.settings_edited('game_display_mode'))
        self.pin.toggled.connect(lambda _: self.settings_edited('persistent_target_topmost'))
        self.player.textEdited.connect(lambda _: self.settings_edited('player_id'))
        layout.addStretch()

    def lobby_page(self):
        _,layout=self.page('大厅助手','赛后返回、转让队长、接受对局；不自动选英雄，不自动开始匹配。')
        self.lobby_dirty=False;self.lobby_state=LobbyStatus()
        frame,inner=panel();layout.addWidget(frame)
        self.lobby_connection=label('尚未检查客户端','heading')
        self.lobby_phase=label('等待客户端','muted');self.lobby_action=label('大厅助手已关闭')
        self.lobby_detail=label('首次使用请分别开启需要的功能。','muted')
        inner.addWidget(self.lobby_connection);inner.addWidget(self.lobby_phase);inner.addWidget(self.lobby_action);inner.addWidget(self.lobby_detail)
        self.lobby_read_button=button('读取当前房间（只读）',self.service.refresh_lobby)
        self.avatar_button=button('定位右上角头像…',self.service.calibrate_avatar)
        self.lobby_save_button=button('保存大厅设置',self.save_lobby_settings,True)
        inner.addLayout(hrow(self.lobby_read_button,self.avatar_button,None,self.lobby_save_button))
        options,content=panel();layout.addWidget(options)
        self.auto_return=QCheckBox('对局结束后自动返回组队房间')
        self.auto_promote=QCheckBox('本人是队长时，自动转让给可接任队友')
        self.auto_accept=QCheckBox('自动接受匹配到的对局')
        for control in [self.auto_return,self.auto_promote,self.auto_accept]:content.addWidget(control)
        self.preferred_member=QComboBox();self.preferred_member.addItem('自动：当前列表首个可接任队友','')
        content.addLayout(hrow(label('接任队友'),self.preferred_member))
        content.addWidget(label('默认按客户端成员顺序选择，跳过本人、机器人与观战者。指定队友未出现时等待，不转给其他人。','muted'))
        self.lobby_accept_delay=QDoubleSpinBox();self.lobby_accept_delay.setRange(0,10);self.lobby_accept_delay.setDecimals(1);self.lobby_accept_delay.setSuffix(' 秒')
        self.lobby_return_delay=QDoubleSpinBox();self.lobby_return_delay.setRange(0,30);self.lobby_return_delay.setDecimals(1);self.lobby_return_delay.setSuffix(' 秒')
        self.lobby_stuck=QDoubleSpinBox();self.lobby_stuck.setRange(10,180);self.lobby_stuck.setDecimals(0);self.lobby_stuck.setSuffix(' 秒')
        self.lobby_rules_hint=label('','muted');content.addWidget(self.lobby_rules_hint)
        self.lobby_advanced_toggle=QCheckBox('高级：调整等待时间与卡结算门槛');content.addWidget(self.lobby_advanced_toggle)
        self.lobby_advanced=QWidget();form=QFormLayout(self.lobby_advanced);form.setVerticalSpacing(12);form.setContentsMargins(0,0,0,0)
        for title,control in [('接受对局前等待',self.lobby_accept_delay),('赛后返回前等待',self.lobby_return_delay),('卡结算恢复门槛',self.lobby_stuck)]:form.addRow(title,control)
        content.addWidget(self.lobby_advanced);self.lobby_advanced.hide();self.lobby_advanced_toggle.toggled.connect(self.lobby_advanced.setVisible)
        self.client_recovery=QCheckBox('返回失败时启用赛后恢复：跳过结算 / 头像回首页');content.addWidget(self.client_recovery)
        content.addWidget(label('只在已确认的赛后阶段且游戏进程退出后执行。头像路径只在卡结算超时后尝试一次，不强制退队、不修改客户端。识别不到头像时先定位，不按固定坐标点击。','muted'))
        self.lobby_notice=label('三个自动功能默认关闭。保存后立即生效；全局暂停 / 退出会取消未执行动作。','notice');layout.addWidget(self.lobby_notice)
        for control in [self.auto_return,self.auto_promote,self.auto_accept,self.client_recovery]:control.toggled.connect(self.lobby_edited)
        for control in [self.lobby_accept_delay,self.lobby_return_delay,self.lobby_stuck]:control.valueChanged.connect(self.lobby_edited)
        self.preferred_member.activated.connect(self.lobby_edited)
        layout.addStretch()

    def lobby_edited(self,*args):
        self.lobby_dirty=True;self.lobby_save_button.setText('保存大厅设置 · 未保存')

    def refresh_lobby_members(self,members):
        preferred=self.preferred_member.currentData() if self.lobby_dirty else self.service.config['lobby_preferred_member']
        self.preferred_member.blockSignals(True);self.preferred_member.clear();self.preferred_member.addItem('自动：当前列表首个可接任队友','')
        for key,name,leader,eligible in members:
            if eligible and not leader:self.preferred_member.addItem(name,key)
        if preferred and self.preferred_member.findData(preferred)<0:self.preferred_member.addItem('指定队友（当前不在房间）',preferred)
        self.preferred_member.setCurrentIndex(max(0,self.preferred_member.findData(preferred)))
        self.preferred_member.blockSignals(False)

    def refresh_lobby_config(self):
        if self.lobby_dirty:return
        config=self.service.config
        self.lobby_rules_hint.setText(f"已保存参数：接受前 {config['lobby_accept_delay']:g} 秒；赛后返回前 {config['lobby_return_delay']:g} 秒；卡结算门槛 {config['lobby_stuck_seconds']:g} 秒。")
        for control,key in [(self.auto_return,'lobby_auto_return'),(self.auto_promote,'lobby_auto_promote'),(self.auto_accept,'lobby_auto_accept'),(self.client_recovery,'lobby_recover_client')]:
            control.blockSignals(True);control.setChecked(config[key]);control.blockSignals(False)
        for control,key in [(self.lobby_accept_delay,'lobby_accept_delay'),(self.lobby_return_delay,'lobby_return_delay'),(self.lobby_stuck,'lobby_stuck_seconds')]:
            control.blockSignals(True);control.setValue(config[key]);control.blockSignals(False)
        self.refresh_lobby_members(self.lobby_state.members)

    def save_lobby_settings(self):
        values=dict(lobby_auto_return=self.auto_return.isChecked(),lobby_auto_promote=self.auto_promote.isChecked(),lobby_auto_accept=self.auto_accept.isChecked(),
            lobby_recover_client=self.client_recovery.isChecked(),lobby_preferred_member=self.preferred_member.currentData() or '',
            lobby_accept_delay=self.lobby_accept_delay.value(),lobby_return_delay=self.lobby_return_delay.value(),lobby_stuck_seconds=self.lobby_stuck.value())
        if self.save_change(values):
            self.lobby_dirty=False;self.lobby_save_button.setText('保存大厅设置')
            self.refresh_lobby_config()
            self.lobby_notice.setText('已保存，即时生效。旧设置下尚未执行的动作取消；已提交动作只回读确认，不连续重发。' if self.service.running else '已保存，继续监测后执行。')
            self.append_log('大厅助手设置已保存；未加入英雄选择或自动开始匹配。')

    def apply_lobby_state(self,value):
        self.lobby_state=value
        self.lobby_connection.setText(value.connection);self.lobby_phase.setText(value.phase)
        self.lobby_action.setText(value.action);self.lobby_detail.setText(value.detail)
        self.lobby_summary.setText('大厅助手：'+value.action.removeprefix('大厅助手'))
        self.refresh_lobby_members(value.members)

    @staticmethod
    def setting_field(widget, hint):
        box = QWidget(); layout = QVBoxLayout(box); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(5)
        widget.setMaximumWidth(250); layout.addWidget(widget); layout.addWidget(label(hint, 'muted')); return box

    def logs_page(self):
        _, layout = self.page('运行日志', '只记录状态变化与必要诊断，不重复记录每次轮询。', scroll=False)
        self.log_categories = QComboBox(); self.log_categories.addItems(['全部类别', '监测', '大厅', '模式', '死亡', '等待', '目标', '窗口', '返回', '配置', '提示'])
        self.log_search = QLineEdit(); self.log_search.setPlaceholderText('筛选日志内容')
        layout.addLayout(hrow(self.log_categories, self.log_search, button('复制所选', self.copy_log)))
        self.log_model = QStandardItemModel(0, 3, self); self.log_model.setHorizontalHeaderLabels(['时间', '类别', '说明'])
        self.log_proxy = LogFilter(self); self.log_proxy.setSourceModel(self.log_model)
        self.log_table = QTableView(); self.log_table.setModel(self.log_proxy); self.setup_table(self.log_table)
        self.log_table.setWordWrap(True); self.log_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.log_table.setColumnWidth(0, 84); self.log_table.setColumnWidth(1, 64); layout.addWidget(self.log_table, 1)
        self.log_categories.currentTextChanged.connect(self.filter_logs); self.log_search.textChanged.connect(self.filter_logs)
        self.log_status = label('可按 Ctrl + C 复制所选行。最多保留最近 1,000 条界面日志。', 'muted'); layout.addWidget(self.log_status)

    @staticmethod
    def setup_table(table):
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True); table.setShowGrid(False)
        table.verticalHeader().hide(); table.verticalHeader().setDefaultSectionSize(40)
        table.horizontalHeader().setStretchLastSection(True)

    def refresh_config(self):
        config = self.service.config
        for b in self.mode_buttons.buttons(): b.setChecked(b.property('mode') == config['mode'])
        for b in self.site_buttons.buttons(): b.setChecked(b.property('site') == config['entertainment_site'])
        self.target_combo.blockSignals(True); self.target_combo.clear()
        for target in config['targets']: self.target_combo.addItem(target['name'], target['id'])
        self.target_combo.setCurrentIndex(self.target_combo.findData(config['study_target'])); self.target_combo.blockSignals(False)
        spec = selected_spec(config); self.target_label.setText(spec['name']); self.bar_target.setText(('学习模式' if config['mode'] == 'study' else '娱乐模式') + '  /  ' + spec['name'])
        self.target_mark.setText('学' if config['mode'] == 'study' else '娱')
        self.refresh_rules()
        study = next(t for t in config['targets'] if t['id'] == config['study_target'])
        self.edit_target_button.setEnabled(study['adapter'] in {'anki', 'desktop'})
        self.custom_summary.setText('当前学习目标：' + study['name'] + '\n' + ('程序与匹配条件可通过上方“编辑目标”调整。' if study['adapter'] != 'web' else '当前使用官方网页目标。'))
        if not self.dirty_settings:
            for field, value in [(self.delay, config['death_switch_delay']), (self.lead, config['return_before_seconds']), (self.threshold, config['gray_threshold'] * 100), (self.frames, config['confirmation_frames']), (self.poll, config['poll_seconds'])]:
                field.blockSignals(True); field.setValue(value); field.blockSignals(False)
            self.display.blockSignals(True); self.display.setCurrentIndex(self.display.findData(config['game_display_mode'])); self.display.blockSignals(False)
            self.pin.blockSignals(True); self.pin.setChecked(config['persistent_target_topmost']); self.pin.blockSignals(False)
            self.player.setText(config['player_id'])
        self.refresh_lobby_config()

    def show_notice(self, message, error=False):
        self.notice.setObjectName('warning' if error else 'notice'); self.notice.setText(message); self.notice.show()
        self.notice.style().unpolish(self.notice); self.notice.style().polish(self.notice)

    def save_change(self, changes=None, target=None):
        try:
            self.service.update_config(changes, target); self.refresh_config()
            return True
        except (ValueError, OSError) as exc:
            self.show_notice(str(exc) if isinstance(exc, ValueError) else '设置未保存，请检查目录写入权限后重试。', True)
            if self.stack.currentIndex() != 0: QMessageBox.warning(self, '未能保存', self.notice.text())
            return False

    def set_mode(self, mode):
        if self.save_change({'mode': mode}): self.show_notice('模式已保存，旧切出任务取消；对局中等待下一次完整死亡。')
        else: self.refresh_config()

    def choose_saved(self):
        self.save_change({'study_target': self.target_combo.currentData()})

    def manage_current(self): self.navigation.setCurrentRow(1 if self.service.config['mode'] == 'study' else 2)

    def open_current(self):
        if self.pending_open: return
        if self.service.reopen_target():
            self.pending_open = True; self.open_button.setEnabled(False); self.open_button.setText('正在打开…')
        else:
            self.show_notice(self.service.config_error or '上一次打开请求尚未结束，请稍后重试。', True)

    def open_entertainment(self):
        if self.save_change({'mode': 'entertainment'}): self.open_current()

    def toggle_pause(self):
        if self.service.running: self.service.stop()
        elif not self.service.start(): self.show_notice(self.service.config_error or '暂时无法继续，请等待当前操作结束。', True)

    def scan(self): self.service.scan()

    def render_apps(self):
        query = self.app_filter.text().casefold()
        items = [i for i in self.detected if query in i['name'].casefold()]
        if self.missing.isChecked():
            found = {i['catalog_id'] for i in self.detected}
            items += [dict(name=e['name'], catalog_id=e['id'], path='', source='仅收录在目录中') for e in self.service.catalog.search(self.app_filter.text(), windows_only=True) if e['id'] not in found]
        self.visible_apps = items; self.app_table.setRowCount(len(items))
        for row, item in enumerate(items):
            for col, text in enumerate([item['name'], '已检测到' if item['path'] else '未检测到', item['source'], item['path'] or '可选择文件补充']):
                cell = QTableWidgetItem(text); cell.setToolTip(text); self.app_table.setItem(row, col, cell)
        for col, width in [(0, 125), (1, 95), (2, 135)]: self.app_table.setColumnWidth(col, width)
        self.update_app_actions()

    def update_app_actions(self):
        row = self.app_table.currentRow()
        self.use_app_button.setEnabled(0 <= row < len(self.visible_apps) and bool(self.visible_apps[row]['path']))

    def select_app(self):
        row = self.app_table.currentRow()
        if not 0 <= row < len(self.visible_apps) or not self.visible_apps[row]['path']: return
        try:
            self.service.choose_detected(self.visible_apps[row]); self.refresh_config()
            self.scan_status.setText('已设为学习目标；首次安全切出时启动。'); self.scan_status.setObjectName('notice')
        except (ValueError, OSError) as exc: self.scan_status.setText('设置失败：' + str(exc))

    def add_custom(self): self.edit_target(None)
    def edit_current(self):
        spec = next(t for t in self.service.config['targets'] if t['id'] == self.service.config['study_target'])
        self.edit_target(spec)

    def edit_target(self, preset):
        dialog = TargetEditor(self, preset)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.save_change(target=dialog.result_spec): self.show_notice('桌面目标已保存，按完整进程路径与窗口条件匹配。')

    def search(self):
        try: self.service.search(self.site_query.text().strip())
        except ValueError as exc: self.search_status.setText(str(exc))

    def render_sites(self, items, kind, name=''):
        self.site_items, self.search_kind, self.search_name = items, kind, name
        self.site_list.clear()
        for item in items:
            if kind == 'catalog':
                badge = '用户确认' if item.get('verification') == 'user_confirmed' else '已核验目录'
                text = item['name'] + '  ·  ' + badge + '\n' + item['category'] + '  /  ' + item['official_url']
            else: text = item['title'] + '  ·  待确认\n' + item['url']
            entry = QListWidgetItem(text); entry.setSizeHint(QSize(300, 64)); self.site_list.addItem(entry)
        self.use_site_button.setEnabled(False); self.site_notes.setText('请选择条目查看支持方式。' if items else '没有合适结果，可换用官方名称重试，或添加本机桌面软件。')

    def site_detail(self, row):
        valid = 0 <= row < len(self.site_items); self.use_site_button.setEnabled(valid)
        self.use_site_button.setText('确认官网并保存打开' if self.search_kind == 'candidates' else '设为目标并打开官网')
        if valid:
            item = self.site_items[row]
            self.site_notes.setText(item.get('note', '搜索候选尚未认证为官方。请核对官方名称、域名和页面内容后再确认。'))

    def select_site(self):
        row = self.site_list.currentRow()
        if not 0 <= row < len(self.site_items): return
        item = self.site_items[row]
        if self.search_kind == 'candidates':
            answer = QMessageBox.question(self, '确认此网址为官网', f'名称：{self.search_name}\n地址：{item["url"]}\n\n这是未核实的搜索候选。确认这是你需要的官方入口后，才会保存并打开。', QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Yes: return
        try:
            self.service.choose_web(item, self.search_name if self.search_kind == 'candidates' else None)
            self.refresh_config(); self.search_status.setText('目标已保存，正在打开独立浏览器窗口。'); self.open_current()
        except (ValueError, OSError) as exc: self.search_status.setText('保存失败：' + str(exc))

    def settings_edited(self, key):
        self.edited_settings.add(key)
        self.dirty_settings = True; self.save_settings_button.setText('保存设置 · 未保存')

    def save_settings(self):
        changes = dict(death_switch_delay=self.delay.value(), return_before_seconds=self.lead.value(),
            gray_threshold=self.threshold.value()/100, confirmation_frames=self.frames.value(), poll_seconds=self.poll.value(),
            player_id=self.player.text().strip(), game_display_mode=self.display.currentData(), persistent_target_topmost=self.pin.isChecked())
        changes = {key: value for key, value in changes.items() if key in self.edited_settings}
        if self.save_change(changes):
            self.dirty_settings = False; self.save_settings_button.setText('保存设置')
            self.edited_settings.clear()
            self.settings_notice.setText('已保存。显示方式即时生效；其余通用参数请暂停后继续以应用。' if self.service.running else '已保存。下次继续监测时使用新参数。')
            self.append_log('设置已保存；公共参数在下一次启动监测时使用。')

    def apply_state(self, state):
        self.state = state
        self.monitor_badge.setText('●  ' + state.monitoring)
        color = '#a26b21' if '不可用' in state.monitoring or '异常' in state.monitoring else '#687d87' if '暂停' in state.monitoring else '#187d73'
        self.monitor_badge.setStyleSheet(f'color:{color};font-weight:600;')
        self.flow_label.setText(state.phase); self.reason_label.setText(state.reason); self.game_label.setText(state.game)
        self.target_label.setText(state.target_name or selected_spec(self.service.config)['name'])
        self.target_status.setText(state.target_state); self.switch_label.setText(state.switch_result); self.pin_label.setText(state.pin)
        self.respawn_label.setVisible(state.respawn is not None)
        if state.respawn is not None: self.respawn_label.setText(f'复活倒计时  {state.respawn:.1f} 秒')
        self.target_hint.setText(state.target_reason)
        self.target_hint.setVisible(bool(state.target_reason) and any(x in state.target_state for x in ['关闭', '失败', '唯一窗口', '未检测到']))
        # 只标出后端明确提供的当前阶段；不伪造完成百分比或已完成步骤。
        stage = {'等待切出延迟': 1, '等待画面确认': 2, '等待目标窗口': 3,
                 '学习中': 3, '娱乐中': 3, '正在返回': 4}.get(state.phase, -1)
        for index, step in enumerate(self.flow_steps):
            step.setProperty('active', index == stage)
            step.setAccessibleDescription('当前阶段' if index == stage else '流程参考')
            step.style().unpolish(step); step.style().polish(step)
        self.refresh_rules()

    def filter_logs(self):
        self.log_proxy.beginFilterChange()
        self.log_proxy.category = self.log_categories.currentText(); self.log_proxy.query = self.log_search.text()
        self.log_proxy.endFilterChange(QSortFilterProxyModel.Direction.Rows)

    @staticmethod
    def category(text):
        if text.startswith('大厅助手'):return '大厅'
        for terms, category in [(['返回'], '返回'), (['模式', '旧目标'], '模式'), (['本人死亡已确认'], '死亡'), (['切出等待', '灰屏', '采样'], '等待'), (['置顶', '切换', '窗口状态'], '窗口'), (['目标', '主窗口', '软件'], '目标'), (['配置', '设置'], '配置'), (['监测', '对局', '接口'], '监测')]:
            if any(word in text for word in terms): return category
        return '提示'

    def append_log(self, text, persist=True, timestamp=None):
        timestamp = timestamp or time.strftime('%H:%M:%S')
        if self.log_model.rowCount() and self.log_model.item(self.log_model.rowCount()-1, 2).text() == text: return
        items = [QStandardItem(value) for value in [timestamp, self.category(text), text]]
        for item in items: item.setEditable(False); item.setToolTip(text)
        self.log_model.appendRow(items)
        if self.log_model.rowCount() > 1000: self.log_model.removeRows(0, 100)
        self.log_table.resizeRowsToContents()
        recent = [self.log_model.item(i, 0).text() + '   ' + self.log_model.item(i, 2).text() for i in range(max(0, self.log_model.rowCount()-3), self.log_model.rowCount())]
        self.recent_label.setText('\n'.join(recent))
        if persist:
            try:
                path = self.service.root / 'runtime.log'
                if path.exists() and path.stat().st_size > 300000: path.write_text('', encoding='utf-8')
                with path.open('a', encoding='utf-8') as file: file.write(timestamp + ' ' + text + '\n')
            except OSError: pass

    def load_recent_log(self):
        try:
            path = self.service.root / 'runtime.log'
            with path.open('rb') as file:
                file.seek(max(0, path.stat().st_size-30000)); lines = file.read().decode('utf-8', errors='replace').splitlines()[-80:]
            for line in lines:
                if len(line) > 9: self.append_log(line[9:], persist=False, timestamp=line[:8])
        except OSError: pass

    def copy_log(self):
        rows = sorted({index.row() for index in self.log_table.selectionModel().selectedRows()})
        if not rows: self.log_status.setText('请先选择需要复制的日志行。'); return
        text = '\n'.join('\t'.join(str(self.log_proxy.index(row, col).data() or '') for col in range(3)) for row in rows)
        QApplication.clipboard().setText(text); self.log_status.setText(f'已复制 {len(rows)} 条日志。')

    def handle_job(self, key, status, result):
        busy, error = status == 'busy', status == 'error'
        if key=='lobby_read':
            self.lobby_read_button.setEnabled(not busy);self.lobby_read_button.setText('正在读取…' if busy else '读取当前房间（只读）')
            if error:self.lobby_notice.setText(result)
            elif not busy:self.apply_lobby_state(result)
        elif key=='avatar_frame':
            self.avatar_button.setEnabled(not busy);self.avatar_button.setText('正在读取客户端画面…' if busy else '定位右上角头像…')
            if error:self.lobby_notice.setText(result)
            elif not busy:
                dialog=AvatarDialog(self,self.service,result)
                if dialog.exec()==QDialog.DialogCode.Accepted:self.lobby_notice.setText('头像定位已保存，仅在已确认的赛后卡加载场景中使用。')
        elif key == 'scan':
            self.scan_button.setEnabled(not busy); self.scan_button.setText('正在检测…' if busy else '重新检测')
            if busy: self.scan_status.setText('正在读取安装登记、快捷方式与实际运行程序…')
            elif error:
                self.scan_status.setText(result + (' 列表保留上次成功的检测结果。' if self.detected else '')); self.append_log(result)
            else:
                self.detected = result; self.render_apps()
                self.scan_status.setText(f'检测完成，找到 {len(result)} 个本机程序。' if result else '检测完成，未找到已收录的学习软件。可用文件选择器添加便携版或其他软件。')
        elif key == 'search':
            self.search_button.setEnabled(not busy); self.search_button.setText('正在查询…' if busy else '查找官网')
            self.site_query.setEnabled(not busy)
            if busy:
                self.search_status.setText('正在按名称查询目录；未收录时联网搜索…'); self.use_site_button.setEnabled(False)
            elif error:
                self.render_sites([], 'candidates'); self.search_status.setText(result); self.append_log(result)
            else:
                self.render_sites(result['items'], result['kind'], result['name'])
                self.search_status.setText(('找到目录条目，请选择具体产品。' if result['kind'] == 'catalog' else '搜索结果待确认，请先核对域名。') if result['items'] else '未找到合适结果。请核对官方名称，稍后重试。')
        elif key == 'inspect' and not busy and not error and result['epoch'] == self.service.epoch:
            self.target_status.setText('未检测到安装程序' if result.get('missing') else result['state']); self.target_hint.setText(result['reason'])
            self.target_hint.setVisible(bool(result['reason']) and (result.get('missing') or any(x in result['state'] for x in ['关闭', '失败', '唯一窗口'])))
        elif key == 'inspect' and error:
            self.target_status.setText('目标检查失败'); self.target_hint.setText(result); self.target_hint.show()
        elif key == 'open' and not busy:
            self.pending_open = False; self.open_button.setEnabled(True); self.open_button.setText('打开 / 重新打开目标')
            if isinstance(result, dict):
                if result['epoch'] != self.service.epoch: return
                result = result['message']
            self.show_notice(result, error); self.append_log(result)

    def tick(self):
        for _ in range(200):
            try: kind, value = self.service.events.get_nowait()
            except queue.Empty: break
            if kind == 'log': self.append_log(value)
            elif kind == 'state': self.apply_state(value)
            elif kind == 'lobby_state': self.apply_lobby_state(value)
            elif kind == 'config': self.refresh_config()
            elif kind == 'selection':
                self.target_label.setText(value); self.target_status.setText('正在检查新目标'); self.target_hint.hide()
                self.pending_open = False; self.open_button.setEnabled(True); self.open_button.setText('打开 / 重新打开目标')
            elif kind == 'job': self.handle_job(*value)
            elif kind == 'download_request':
                reply, answer, cancelled = value
                if cancelled.is_set() or self.closing or self.service.stop_event.is_set(): reply.set()
                else:
                    dialog = DownloadPrompt(self, self.service, value); self.prompts.append(dialog); dialog.show(); dialog.raise_()
        self.prompts = [p for p in self.prompts if p.isVisible()]
        pressed = self.service.windows.stop_pressed()
        if pressed and not self.hotkey_down: self.service.stop()
        self.hotkey_down = pressed
        running = self.service.running
        stopping = running and self.service.stop_event.is_set()
        self.pause_button.setText('正在暂停…' if stopping else ('暂停监测' if running else '继续监测'))
        self.pause_button.setEnabled(not stopping and not self.service.config_error and not self.closing)
        if time.monotonic() - self.last_inspection > 2 and not self.closing:
            self.last_inspection = time.monotonic(); self.service.inspect_target()
        if self.closing and self.service.settled():
            self.timer.stop(); self.close()

    def closeEvent(self, event):
        if self.closing and self.service.settled(): event.accept(); return
        self.closing = True; self.service.close()
        for prompt in self.prompts: prompt.finish(False)
        self.monitor_badge.setText('●  正在恢复窗口状态并退出…'); event.ignore()
