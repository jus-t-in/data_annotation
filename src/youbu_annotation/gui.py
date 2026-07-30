"""PySide6/pyqtgraph desktop editor for one protocol trial."""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Callable

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .auto_adapter import recognize
from .constants import (
    ACTIVITY_NAMES,
    APP_NAME,
    IMPACT_COLOR,
    LEFT_COLOR,
    MOTION_COLOR,
    PITCH_COLOR,
    RIGHT_COLOR,
    STATE_COLORS,
    TERRAIN_NAMES,
    VERSION,
    state_name,
)
from .model import (
    AnnotationDocument,
    Boundary,
    Confirmation,
    ConfirmationKind,
    Provenance,
    Severity,
    Track,
)
from .recovery import RecoveryConflictError, RecoveryStore
from .renderer import render_annotation_png
from .storage import AnnotationRepository, AnnotationStorageError
from .trial import TrialData, TrialValidationError


KIND_NAMES = {
    "initial": "起始",
    "boundary": "边界",
    ConfirmationKind.STAIR_SECOND_STEP.value: "第二步确认",
    ConfirmationKind.TRIAL_END.value: "收尾确认",
}
SOURCE_NAMES = {
    Provenance.AUTO: "自动",
    Provenance.ADJUSTED: "已调整",
    Provenance.MANUAL: "人工",
    Provenance.IMPORTED_UNKNOWN: "来源不明",
}


class EventLabel(pg.TextItem):
    clicked = QtCore.Signal(str)

    def __init__(self, component_id: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.component_id = component_id
        self.setAcceptedMouseButtons(QtCore.Qt.MouseButton.LeftButton)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit(self.component_id)
            event.accept()
            return
        super().mousePressEvent(event)


class TaskSignals(QtCore.QObject):
    finished = QtCore.Signal(object)
    failed = QtCore.Signal(str, str)


class BackgroundTask(QtCore.QRunnable):
    def __init__(self, function: Callable[[], object]):
        super().__init__()
        self.function = function
        self.signals = TaskSignals()

    @QtCore.Slot()
    def run(self) -> None:
        try:
            result = self.function()
        except Exception as exc:  # reported on the GUI thread
            self.signals.failed.emit(str(exc), traceback.format_exc())
        else:
            self.signals.finished.emit(result)


class AnnotationEditor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        pg.setConfigOptions(antialias=False, background="#FAFAFA", foreground="#37474F")
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)
        self.settings = QtCore.QSettings("Youbu", "AnnotationEditor")
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._workers: set[BackgroundTask] = set()
        self.trial: TrialData | None = None
        self.document: AnnotationDocument | None = None
        self.repository: AnnotationRepository | None = None
        self.recovery: RecoveryStore | None = None
        self.cursor_index = 0
        self.selected_event_id: str | None = None
        self.selected_component_id: str | None = None
        self.interval_start: int | None = None
        self.interval_end: int | None = None
        self._refreshing = False
        self._syncing_range = False
        self._syncing_cursor = False
        self._busy = False
        self._state_items: list[object] = []
        self._main_annotation_items: list[object] = []
        self._markers: dict[str, pg.InfiniteLine] = {}
        self._cursor_lines: list[tuple[pg.PlotItem, pg.InfiniteLine]] = []
        self._raw_curves: list[pg.PlotDataItem] = []
        self._build_actions()
        self._build_ui()
        self._apply_style()
        self._connect_actions()
        self._set_session_enabled(False)
        self.autosave_timer = QtCore.QTimer(self)
        self.autosave_timer.setInterval(10_000)
        self.autosave_timer.timeout.connect(self._autosave_recovery)
        self.autosave_timer.start()

    def _icon(self, standard: QtWidgets.QStyle.StandardPixmap) -> QtGui.QIcon:
        return self.style().standardIcon(standard)

    def _build_actions(self) -> None:
        style = QtWidgets.QStyle.StandardPixmap
        self.open_action = QtGui.QAction(self._icon(style.SP_DialogOpenButton), "打开试次", self)
        self.open_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        self.target_action = QtGui.QAction(self._icon(style.SP_DirOpenIcon), "选择标注文件", self)
        self.save_action = QtGui.QAction(self._icon(style.SP_DialogSaveButton), "正式保存", self)
        self.save_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        self.export_action = QtGui.QAction(self._icon(style.SP_DriveFDIcon), "导出 PNG", self)
        self.undo_action = QtGui.QAction(self._icon(style.SP_ArrowBack), "撤销", self)
        self.undo_action.setShortcut(QtGui.QKeySequence.StandardKey.Undo)
        self.redo_action = QtGui.QAction(self._icon(style.SP_ArrowForward), "重做", self)
        self.redo_action.setShortcut(QtGui.QKeySequence.StandardKey.Redo)
        self.recognize_action = QtGui.QAction(self._icon(style.SP_BrowserReload), "重新识别", self)
        self.delete_action = QtGui.QAction(self._icon(style.SP_TrashIcon), "删除所选分量", self)
        self.delete_action.setShortcut(QtGui.QKeySequence.StandardKey.Delete)
        self.auxiliary_actions: dict[str, QtGui.QAction] = {}
        for name, label, default in (
            ("overview", "全局概览", True),
            ("pitch", "俯仰", False),
            ("motion", "运动/冲击", False),
        ):
            action = QtGui.QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.settings.value(f"plots/{name}", default, type=bool))
            self.auxiliary_actions[name] = action

        file_menu = self.menuBar().addMenu("文件")
        file_menu.addActions((self.open_action, self.target_action, self.save_action, self.export_action))
        file_menu.addSeparator()
        file_menu.addAction("退出", self.close, QtGui.QKeySequence.StandardKey.Quit)
        edit_menu = self.menuBar().addMenu("编辑")
        edit_menu.addActions((self.undo_action, self.redo_action, self.delete_action, self.recognize_action))
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("关于", self._about)

        toolbar = self.addToolBar("主要操作")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toolbar.addActions(
            (
                self.open_action,
                self.target_action,
                self.save_action,
                self.undo_action,
                self.redo_action,
                self.recognize_action,
                self.export_action,
            )
        )
        self.session_actions = [
            self.target_action,
            self.save_action,
            self.export_action,
            self.undo_action,
            self.redo_action,
            self.recognize_action,
            self.delete_action,
        ]

    def _build_ui(self) -> None:
        root = QtWidgets.QWidget()
        root.setObjectName("centralRoot")
        root_layout = QtWidgets.QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QtWidgets.QFrame()
        header.setObjectName("header")
        header_layout = QtWidgets.QGridLayout(header)
        header_layout.setContentsMargins(12, 7, 12, 7)
        self.source_label = QtWidgets.QLabel("未打开试次")
        self.source_label.setObjectName("sourceLabel")
        self.source_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.target_label = QtWidgets.QLabel("标注文件：未选择")
        self.target_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.target_label.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Preferred)
        self.review_badge = QtWidgets.QLabel("未载入")
        self.review_badge.setObjectName("reviewBadge")
        self.raw_checkbox = QtWidgets.QCheckBox("原始通道")
        self.auxiliary_button = QtWidgets.QToolButton()
        self.auxiliary_button.setObjectName("auxiliaryButton")
        self.auxiliary_button.setText("辅助图")
        self.auxiliary_button.setIcon(self._icon(QtWidgets.QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.auxiliary_button.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.auxiliary_button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        auxiliary_menu = QtWidgets.QMenu(self.auxiliary_button)
        auxiliary_menu.addActions(tuple(self.auxiliary_actions.values()))
        self.auxiliary_button.setMenu(auxiliary_menu)
        header_layout.addWidget(self.source_label, 0, 0)
        header_layout.addWidget(self.review_badge, 0, 1, 2, 1, QtCore.Qt.AlignmentFlag.AlignRight)
        header_layout.addWidget(self.target_label, 1, 0)
        header_layout.addWidget(self.raw_checkbox, 0, 2, 2, 1)
        header_layout.addWidget(self.auxiliary_button, 0, 3, 2, 1)
        header_layout.setColumnStretch(0, 1)
        root_layout.addWidget(header)

        self.main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.left_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.left_splitter.setChildrenCollapsible(False)
        self.graphics = pg.GraphicsLayoutWidget()
        self.overview_plot = self.graphics.addPlot(row=0, col=0)
        self.annotation_plot = self.graphics.addPlot(row=1, col=0)
        self.leg_plot = self.graphics.addPlot(row=2, col=0)
        self.pitch_plot = self.graphics.addPlot(row=3, col=0)
        self.motion_plot = self.graphics.addPlot(row=4, col=0)
        self.state_plot = self.graphics.addPlot(row=5, col=0)
        self.graphics.ci.layout.setRowFixedHeight(1, 84)
        self.graphics.ci.layout.setRowFixedHeight(5, 92)
        self._auxiliary_plots = {
            "overview": (self.overview_plot, 0, 38),
            "pitch": (self.pitch_plot, 3, 48),
            "motion": (self.motion_plot, 4, 48),
        }
        self._configure_plots()
        self._apply_auxiliary_visibility()
        self.left_splitter.addWidget(self.graphics)

        self.event_table = QtWidgets.QTableWidget(0, 6)
        self.event_table.setHorizontalHeaderLabels(("时刻", "类型", "活动", "地形", "来源", "备注"))
        self.event_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.event_table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.event_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.event_table.setAlternatingRowColors(True)
        self.event_table.setShowGrid(False)
        self.event_table.verticalHeader().hide()
        self.event_table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self.event_table.horizontalHeader().setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.left_splitter.addWidget(self.event_table)
        self.left_splitter.setSizes([720, 120])
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self._build_inspector())
        self.main_splitter.setSizes([1080, 340])
        self.main_splitter.setStretchFactor(0, 1)
        root_layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(root)

        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(150)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

    def _build_inspector(self) -> QtWidgets.QWidget:
        content = QtWidgets.QWidget()
        content.setObjectName("inspector")
        layout = QtWidgets.QVBoxLayout(content)
        layout.setContentsMargins(10, 10, 10, 12)
        layout.setSpacing(9)

        selected = QtWidgets.QGroupBox("所选事件")
        selected_form = QtWidgets.QFormLayout(selected)
        self.timestamp_label = QtWidgets.QLabel("-")
        self.timestamp_label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self.component_combo = QtWidgets.QComboBox()
        time_row = QtWidgets.QWidget()
        time_layout = QtWidgets.QHBoxLayout(time_row)
        time_layout.setContentsMargins(0, 0, 0, 0)
        self.previous_sample_button = QtWidgets.QToolButton()
        self.previous_sample_button.setIcon(self._icon(QtWidgets.QStyle.StandardPixmap.SP_ArrowLeft))
        self.previous_sample_button.setToolTip("前一个真实采样点")
        self.time_spin = QtWidgets.QDoubleSpinBox()
        self.time_spin.setDecimals(6)
        self.time_spin.setKeyboardTracking(False)
        self.time_spin.setSuffix(" s")
        self.next_sample_button = QtWidgets.QToolButton()
        self.next_sample_button.setIcon(self._icon(QtWidgets.QStyle.StandardPixmap.SP_ArrowRight))
        self.next_sample_button.setToolTip("后一个真实采样点")
        time_layout.addWidget(self.previous_sample_button)
        time_layout.addWidget(self.time_spin, 1)
        time_layout.addWidget(self.next_sample_button)
        self.value_combo = QtWidgets.QComboBox()
        self.note_edit = QtWidgets.QLineEdit()
        self.note_edit.setMaxLength(500)
        self.evidence_label = QtWidgets.QLabel("-")
        self.evidence_label.setWordWrap(True)
        self.evidence_label.setObjectName("evidence")
        self.delete_button = QtWidgets.QPushButton(self.delete_action.icon(), "删除分量")
        self.delete_button.setObjectName("dangerButton")
        selected_form.addRow("真实时刻", self.timestamp_label)
        selected_form.addRow("事件分量", self.component_combo)
        selected_form.addRow("相对时刻", time_row)
        selected_form.addRow("标签", self.value_combo)
        selected_form.addRow("判断依据", self.note_edit)
        selected_form.addRow("采样证据", self.evidence_label)
        selected_form.addRow(self.delete_button)
        layout.addWidget(selected)

        create = QtWidgets.QGroupBox("新建")
        create_form = QtWidgets.QFormLayout(create)
        self.cursor_label = QtWidgets.QLabel("-")
        activity_row = QtWidgets.QWidget()
        activity_layout = QtWidgets.QHBoxLayout(activity_row)
        activity_layout.setContentsMargins(0, 0, 0, 0)
        self.new_activity_combo = self._label_combo(ACTIVITY_NAMES)
        self.add_activity_button = QtWidgets.QPushButton("添加活动边界")
        activity_layout.addWidget(self.new_activity_combo, 1)
        activity_layout.addWidget(self.add_activity_button)
        terrain_row = QtWidgets.QWidget()
        terrain_layout = QtWidgets.QHBoxLayout(terrain_row)
        terrain_layout.setContentsMargins(0, 0, 0, 0)
        self.new_terrain_combo = self._label_combo(TERRAIN_NAMES)
        self.add_terrain_button = QtWidgets.QPushButton("添加地形边界")
        terrain_layout.addWidget(self.new_terrain_combo, 1)
        terrain_layout.addWidget(self.add_terrain_button)
        confirmation_row = QtWidgets.QWidget()
        confirmation_layout = QtWidgets.QHBoxLayout(confirmation_row)
        confirmation_layout.setContentsMargins(0, 0, 0, 0)
        self.confirmation_combo = QtWidgets.QComboBox()
        self.confirmation_combo.addItem("楼梯第二步确认", ConfirmationKind.STAIR_SECOND_STEP)
        self.confirmation_combo.addItem("试次收尾确认", ConfirmationKind.TRIAL_END)
        self.add_confirmation_button = QtWidgets.QPushButton("添加确认")
        confirmation_layout.addWidget(self.confirmation_combo, 1)
        confirmation_layout.addWidget(self.add_confirmation_button)
        create_form.addRow("光标", self.cursor_label)
        create_form.addRow("活动", activity_row)
        create_form.addRow("地形", terrain_row)
        create_form.addRow("确认", confirmation_row)

        interval_row = QtWidgets.QWidget()
        interval_layout = QtWidgets.QGridLayout(interval_row)
        interval_layout.setContentsMargins(0, 0, 0, 0)
        self.interval_track_combo = QtWidgets.QComboBox()
        self.interval_track_combo.addItem("活动", Track.ACTIVITY)
        self.interval_track_combo.addItem("地形", Track.TERRAIN)
        self.interval_value_combo = QtWidgets.QComboBox()
        self.interval_start_button = QtWidgets.QPushButton("设为开始")
        self.interval_end_button = QtWidgets.QPushButton("设为结束")
        self.interval_add_button = QtWidgets.QPushButton("添加区间")
        self.interval_range_label = QtWidgets.QLabel("未设置")
        interval_layout.addWidget(self.interval_track_combo, 0, 0)
        interval_layout.addWidget(self.interval_value_combo, 0, 1, 1, 2)
        interval_layout.addWidget(self.interval_start_button, 1, 0)
        interval_layout.addWidget(self.interval_end_button, 1, 1)
        interval_layout.addWidget(self.interval_add_button, 1, 2)
        interval_layout.addWidget(self.interval_range_label, 2, 0, 1, 3)
        create_form.addRow("区间", interval_row)
        layout.addWidget(create)

        qa = QtWidgets.QGroupBox("QA 复核")
        qa_layout = QtWidgets.QVBoxLayout(qa)
        self.qa_list = QtWidgets.QListWidget()
        self.qa_list.setMaximumHeight(130)
        qa_buttons = QtWidgets.QHBoxLayout()
        self.qa_fixed_button = QtWidgets.QPushButton("标记已修正")
        self.qa_accept_button = QtWidgets.QPushButton("确认无需修改")
        qa_buttons.addWidget(self.qa_fixed_button)
        qa_buttons.addWidget(self.qa_accept_button)
        qa_layout.addWidget(self.qa_list)
        qa_layout.addLayout(qa_buttons)
        layout.addWidget(qa)

        review = QtWidgets.QGroupBox("正式复核")
        review_form = QtWidgets.QFormLayout(review)
        self.annotator_edit = QtWidgets.QLineEdit(str(self.settings.value("annotator_id", "")))
        self.review_checkbox = QtWidgets.QCheckBox("我已检查完整时间线")
        self.validation_label = QtWidgets.QLabel("-")
        self.validation_label.setWordWrap(True)
        self.validation_label.setObjectName("validation")
        review_form.addRow("标注员 ID", self.annotator_edit)
        review_form.addRow(self.review_checkbox)
        review_form.addRow(self.validation_label)
        layout.addWidget(review)
        layout.addStretch(1)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        scroll.setMinimumWidth(320)
        return scroll

    @staticmethod
    def _label_combo(labels: dict[str, str]) -> QtWidgets.QComboBox:
        combo = QtWidgets.QComboBox()
        for value, display in labels.items():
            combo.addItem(display, value)
        return combo

    def _configure_plots(self) -> None:
        self.overview_plot.setMenuEnabled(False)
        self.overview_plot.hideAxis("left")
        self.overview_plot.hideAxis("bottom")
        self.overview_plot.setMouseEnabled(x=True, y=False)
        self.annotation_plot.setMenuEnabled(False)
        self.annotation_plot.setMouseEnabled(x=True, y=False)
        self.annotation_plot.setYRange(0, 3, padding=0)
        self.annotation_plot.setLabel("left", "标注")
        self.annotation_plot.getAxis("left").setTicks([])
        self.annotation_plot.hideAxis("bottom")
        plots = (self.leg_plot, self.pitch_plot, self.motion_plot, self.state_plot)
        for plot in plots:
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.showGrid(x=False, y=True, alpha=0.22)
            plot.setClipToView(True)
            plot.setDownsampling(auto=True, mode="peak")
        self.leg_plot.setLabel("left", "腿部位置")
        self.pitch_plot.setLabel("left", "俯仰")
        self.motion_plot.setLabel("left", "运动/冲击")
        self.state_plot.setLabel("bottom", "试次内相对时间", units="s")
        self.state_plot.getAxis("left").setTicks([[(0.45, "地形"), (1.45, "活动"), (2.45, "确认")]])
        self.state_plot.setYRange(0, 3, padding=0)
        self.state_plot.setMouseEnabled(x=True, y=False)
        for plot in (self.annotation_plot, self.pitch_plot, self.motion_plot, self.state_plot):
            plot.setXLink(self.leg_plot)
        for plot in (self.annotation_plot, self.leg_plot, self.pitch_plot, self.motion_plot, self.state_plot):
            plot.getAxis("left").setWidth(62)
            for axis_name in ("left", "bottom"):
                axis = plot.getAxis(axis_name)
                axis.setPen(pg.mkPen("#aeb4b7", width=0.8))
                axis.setTextPen(pg.mkPen("#3e4548"))
                axis.setTickFont(QtGui.QFont("Noto Sans CJK SC", 8))
        for plot in (self.leg_plot, self.pitch_plot, self.motion_plot):
            plot.hideAxis("bottom")
        legend = self.leg_plot.addLegend(offset=(10, 8), labelTextColor="#303638")
        legend.setBrush(pg.mkBrush(255, 255, 255, 215))
        legend.setPen(pg.mkPen("#c9cdcf", width=0.7))
        self.leg_plot.sigXRangeChanged.connect(self._detail_range_changed)
        self.graphics.scene().sigMouseClicked.connect(self._plot_clicked)

    def _apply_auxiliary_visibility(self) -> None:
        for name, action in self.auxiliary_actions.items():
            self._set_auxiliary_visible(name, action.isChecked(), persist=False)

    def _set_auxiliary_visible(self, name: str, visible: bool, *, persist: bool = True) -> None:
        plot, row, height = self._auxiliary_plots[name]
        plot.setVisible(visible)
        self.graphics.ci.layout.setRowFixedHeight(row, height if visible else 0)
        self.graphics.ci.layout.invalidate()
        if persist:
            self.settings.setValue(f"plots/{name}", visible)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { color: #24292c; font-family: "Noto Sans CJK SC", sans-serif; font-size: 12px; }
            QMainWindow, QWidget#centralRoot { background: #f2f3f3; }
            QFrame#header { background: #ffffff; border-bottom: 1px solid #c8ccce; }
            QLabel#sourceLabel { font-size: 15px; font-weight: 600; color: #1f2528; }
            QLabel#reviewBadge { padding: 3px 8px; border: 1px solid #8d969a; border-radius: 3px; background: #f4f5f5; }
            QWidget#inspector, QScrollArea, QScrollArea > QWidget > QWidget { background: #ffffff; }
            QScrollArea { border: 0; border-left: 1px solid #c8ccce; }
            QGroupBox { background: #ffffff; border: 0; border-top: 1px solid #d7d9da; margin-top: 13px; padding-top: 10px; font-weight: 600; }
            QGroupBox::title { subcontrol-origin: margin; left: 0; padding: 0 5px 0 0; background: #ffffff; }
            QLineEdit, QComboBox, QDoubleSpinBox, QListWidget, QTableWidget { background: #ffffff; border: 1px solid #aeb4b7; border-radius: 2px; selection-background-color: #356f72; selection-color: #ffffff; }
            QLineEdit, QComboBox, QDoubleSpinBox { min-height: 25px; padding: 1px 5px; }
            QComboBox::drop-down { border: 0; width: 22px; }
            QListWidget, QTableWidget { alternate-background-color: #f6f7f7; outline: 0; }
            QPushButton, QToolButton { background: #ffffff; border: 1px solid #969da0; border-radius: 3px; min-height: 25px; padding: 2px 7px; }
            QPushButton:hover, QToolButton:hover { background: #edf4f2; border-color: #4f7774; }
            QPushButton:pressed, QToolButton:pressed { background: #dde9e6; }
            QPushButton:disabled, QToolButton:disabled { color: #9ca1a3; background: #f0f1f1; border-color: #d4d6d7; }
            QPushButton#dangerButton { color: #a3312b; border-color: #c69a96; }
            QPushButton#dangerButton:hover { background: #fbefee; border-color: #a3312b; }
            QLabel#evidence { color: #505b60; font-size: 11px; }
            QLabel#validation { color: #a3312b; }
            QHeaderView::section { background: #e9ebeb; padding: 5px; border: 0; border-right: 1px solid #c8ccce; border-bottom: 1px solid #c8ccce; font-weight: 600; }
            QToolBar, QMenuBar, QStatusBar { background: #f7f8f8; }
            QToolBar { border-bottom: 1px solid #c8ccce; spacing: 2px; padding: 2px 5px; }
            QMenuBar { border-bottom: 1px solid #d5d8d9; }
            QMenu { background: #ffffff; border: 1px solid #aeb4b7; padding: 3px; }
            QMenu::item { padding: 5px 24px 5px 8px; }
            QMenu::item:selected { background: #e6efed; color: #1f2528; }
            QSplitter::handle { background: #c8ccce; }
            QSplitter::handle:horizontal { width: 1px; }
            QSplitter::handle:vertical { height: 1px; }
            """
        )

    def _connect_actions(self) -> None:
        self.open_action.triggered.connect(self.request_open)
        self.target_action.triggered.connect(self.choose_target)
        self.save_action.triggered.connect(self.save_formal)
        self.export_action.triggered.connect(self.export_png)
        self.undo_action.triggered.connect(self.undo)
        self.redo_action.triggered.connect(self.redo)
        self.recognize_action.triggered.connect(self.rerun_recognition)
        self.delete_action.triggered.connect(self.delete_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.raw_checkbox.toggled.connect(self._toggle_raw)
        for name, action in self.auxiliary_actions.items():
            action.toggled.connect(partial(self._set_auxiliary_visible, name))
        self.event_table.currentCellChanged.connect(self._table_selection_changed)
        self.component_combo.currentIndexChanged.connect(self._component_changed)
        self.time_spin.editingFinished.connect(self._time_edited)
        self.previous_sample_button.clicked.connect(lambda: self._step_selected(-1))
        self.next_sample_button.clicked.connect(lambda: self._step_selected(1))
        self.value_combo.activated.connect(self._value_edited)
        self.note_edit.editingFinished.connect(self._note_edited)
        self.add_activity_button.clicked.connect(lambda: self._add_boundary(Track.ACTIVITY))
        self.add_terrain_button.clicked.connect(lambda: self._add_boundary(Track.TERRAIN))
        self.add_confirmation_button.clicked.connect(self._add_confirmation)
        self.interval_track_combo.currentIndexChanged.connect(self._update_interval_values)
        self.interval_start_button.clicked.connect(lambda: self._set_interval_edge(True))
        self.interval_end_button.clicked.connect(lambda: self._set_interval_edge(False))
        self.interval_add_button.clicked.connect(self._add_interval)
        self.qa_fixed_button.clicked.connect(lambda: self._resolve_qa("fixed"))
        self.qa_accept_button.clicked.connect(lambda: self._resolve_qa("accepted"))
        self.qa_list.currentItemChanged.connect(self._qa_selection_changed)
        self.review_checkbox.toggled.connect(self._review_toggled)
        self._update_interval_values()

    def _set_session_enabled(self, enabled: bool) -> None:
        for action in self.session_actions:
            action.setEnabled(enabled)
        self.raw_checkbox.setEnabled(enabled)
        self.event_table.setEnabled(enabled)

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.progress.setVisible(busy)
        self.open_action.setEnabled(not busy)
        if busy:
            for action in self.session_actions:
                action.setEnabled(False)
        else:
            self._update_status()
        if message:
            self.statusBar().showMessage(message)

    def _run_task(self, message: str, function: Callable[[], object], success: Callable[[object], None]) -> None:
        if self._busy:
            return
        self._set_busy(True, message)
        worker = BackgroundTask(function)
        self._workers.add(worker)

        def finished(result: object) -> None:
            self._workers.discard(worker)
            self._set_busy(False)
            success(result)

        def failed(error: str, details: str) -> None:
            self._workers.discard(worker)
            self._set_busy(False)
            dialog = QtWidgets.QMessageBox(self)
            dialog.setIcon(QtWidgets.QMessageBox.Icon.Critical)
            dialog.setWindowTitle("操作失败")
            dialog.setText(error)
            dialog.setDetailedText(details)
            dialog.exec()

        worker.signals.finished.connect(finished)
        worker.signals.failed.connect(failed)
        self.thread_pool.start(worker)

    def request_open(self) -> None:
        initial = str(self.settings.value("last_source_dir", Path.cwd()))
        name, _ = QtWidgets.QFileDialog.getOpenFileName(self, "打开原始试次 CSV", initial, "CSV 文件 (*.csv)")
        if name:
            self.open_trial(Path(name))

    def open_trial(self, path: Path, target: Path | None = None) -> None:
        if not self._confirm_leave():
            return
        self._dispose_session()
        path = path.expanduser().resolve()
        self.settings.setValue("last_source_dir", str(path.parent))
        self._run_task("正在读取并归一化试次...", lambda: TrialData.load(path), partial(self._trial_loaded, target=target))

    def _trial_loaded(self, result: object, *, target: Path | None) -> None:
        trial = result
        assert isinstance(trial, TrialData)
        target = (target or trial.path.parent / "state_changes.csv").expanduser().resolve()
        try:
            repository = AnnotationRepository(target, trial)
        except AnnotationStorageError as exc:
            QtWidgets.QMessageBox.critical(self, "无法打开标注文件", str(exc))
            return
        recovery = RecoveryStore(trial, target)
        document = self._choose_recovery(recovery)
        if document is False:
            repository.close()
            return
        if document is None:
            try:
                document = repository.load_document()
            except AnnotationStorageError as exc:
                repository.close()
                QtWidgets.QMessageBox.critical(self, "无法读取标注", str(exc))
                return
        self.trial = trial
        self.repository = repository
        self.recovery = recovery
        self.source_label.setText(f"{trial.session_id} / {trial.trial_id}    {trial.path.name}")
        self.source_label.setToolTip(str(trial.path))
        self.target_label.setText(f"标注文件：{target}")
        self.target_label.setToolTip(str(target))
        self._plot_trial()
        if document is None:
            self.document = None
            self._refresh()
            self._run_task("正在自动识别状态边界...", lambda: recognize(trial), self._initial_recognition_finished)
        else:
            self.document = document
            self._refresh()
            self.statusBar().showMessage("已优先载入现有标注", 5000)

    def _choose_recovery(self, recovery: RecoveryStore) -> AnnotationDocument | bool | None:
        metadata = recovery.metadata()
        if metadata is None:
            return None
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Question)
        dialog.setWindowTitle("发现恢复草稿")
        dialog.setText(f"发现 {metadata.get('saved_at', '未知时间')} 的未正式保存草稿。")
        recover_button = dialog.addButton("恢复草稿", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton("舍弃草稿", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if dialog.clickedButton() is recover_button:
            try:
                return recovery.load()
            except RecoveryConflictError as exc:
                QtWidgets.QMessageBox.critical(self, "恢复冲突", str(exc))
                return False
        if dialog.clickedButton() is discard_button:
            recovery.clear()
            return None
        return False

    def _initial_recognition_finished(self, result: object) -> None:
        if not isinstance(result, AnnotationDocument) or self.trial is not result.trial:
            return
        self.document = result
        self._refresh()
        self.statusBar().showMessage("自动识别完成，结果为待复核草稿", 5000)

    def load_session(
        self,
        trial: TrialData,
        document: AnnotationDocument,
        repository: AnnotationRepository | None = None,
    ) -> None:
        """Inject a prepared session for tests and embedded use."""
        self._dispose_session()
        self.trial = trial
        self.document = document
        self.repository = repository
        self.recovery = RecoveryStore(trial, repository.target) if repository else None
        self.source_label.setText(f"{trial.session_id} / {trial.trial_id}    {trial.path.name}")
        self.target_label.setText(f"标注文件：{repository.target}" if repository else "标注文件：未连接")
        self._plot_trial()
        self._refresh()

    def choose_target(self) -> None:
        if self.trial is None or self.document is None:
            return
        initial = str(self.repository.target if self.repository else self.trial.path.parent / "state_changes.csv")
        name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "选择聚合标注文件", initial, "CSV 文件 (*.csv)")
        if not name:
            return
        target = Path(name).expanduser().resolve()
        if self.repository and target == self.repository.target:
            return
        try:
            repository = AnnotationRepository(target, self.trial)
        except AnnotationStorageError as exc:
            QtWidgets.QMessageBox.critical(self, "无法选择标注文件", str(exc))
            return
        recovery = RecoveryStore(self.trial, target)
        incoming = self._choose_recovery(recovery)
        if incoming is False:
            repository.close()
            return
        if incoming is None:
            try:
                incoming = repository.load_document()
            except AnnotationStorageError as exc:
                repository.close()
                QtWidgets.QMessageBox.critical(self, "无法读取标注", str(exc))
                return
        if incoming is not None and self.document.dirty:
            answer = QtWidgets.QMessageBox.question(
                self,
                "目标已有当前试次标注",
                "目标文件中的现有标注将优先载入并替换当前草稿。是否继续？",
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                repository.close()
                return
        old_repository = self.repository
        self.repository = repository
        self.recovery = recovery
        if incoming is not None:
            self.document = incoming
        if old_repository:
            old_repository.close()
        self.target_label.setText(f"标注文件：{target}")
        self.target_label.setToolTip(str(target))
        self._refresh()

    def _plot_trial(self) -> None:
        assert self.trial is not None
        trial = self.trial
        self._clear_cursor_lines()
        for plot in (
            self.overview_plot,
            self.annotation_plot,
            self.leg_plot,
            self.pitch_plot,
            self.motion_plot,
            self.state_plot,
        ):
            plot.clear()
        self._main_annotation_items.clear()
        x = trial.seconds
        plot_options = {"autoDownsample": True}
        self.overview_plot.plot(x, trial.channels["display/left"], pen=pg.mkPen(LEFT_COLOR, width=1), **plot_options)
        self.overview_plot.plot(x, trial.channels["display/right"], pen=pg.mkPen(RIGHT_COLOR, width=1), **plot_options)
        self.leg_plot.plot(x, trial.channels["display/left"], pen=pg.mkPen(LEFT_COLOR, width=1.2), name="左腿", **plot_options)
        self.leg_plot.plot(x, trial.channels["display/right"], pen=pg.mkPen(RIGHT_COLOR, width=1.2), name="右腿", **plot_options)
        self.pitch_plot.plot(x, trial.channels["display/pitch"], pen=pg.mkPen(PITCH_COLOR, width=1.1), **plot_options)
        self.motion_plot.plot(x, trial.channels["display/motion"], pen=pg.mkPen(MOTION_COLOR, width=1), **plot_options)
        self.motion_plot.plot(x, trial.channels["display/impact"], pen=pg.mkPen(IMPACT_COLOR, width=1), **plot_options)
        self._raw_curves = [
            self.leg_plot.plot(x, trial.channels["display/left_raw"], pen=pg.mkPen(LEFT_COLOR, width=0.6, style=QtCore.Qt.PenStyle.DashLine), **plot_options),
            self.leg_plot.plot(x, trial.channels["display/right_raw"], pen=pg.mkPen(RIGHT_COLOR, width=0.6, style=QtCore.Qt.PenStyle.DashLine), **plot_options),
            self.pitch_plot.plot(x, trial.channels["display/pitch_raw"], pen=pg.mkPen("#9E9E9E", width=0.6, style=QtCore.Qt.PenStyle.DashLine), **plot_options),
        ]
        for plot in (self.overview_plot, self.leg_plot, self.pitch_plot, self.motion_plot):
            for curve in plot.listDataItems():
                curve.setClipToView(True)
        self._toggle_raw(self.raw_checkbox.isChecked())
        initial_end = min(trial.duration, max(20.0, trial.duration * 0.18))
        self.overview_region = pg.LinearRegionItem(
            values=(0.0, initial_end),
            bounds=(0.0, trial.duration),
            movable=True,
            brush=pg.mkBrush(0, 121, 107, 35),
            pen=pg.mkPen("#00796B", width=1),
        )
        self.overview_plot.addItem(self.overview_region)
        self.overview_region.sigRegionChanged.connect(self._overview_region_changed)
        self.overview_plot.setXRange(0, trial.duration, padding=0)
        self.leg_plot.setXRange(0, initial_end, padding=0)
        for gap in trial.gaps:
            for plot in (self.overview_plot, self.leg_plot, self.pitch_plot, self.motion_plot):
                region = pg.LinearRegionItem(
                    values=(gap.start, gap.end),
                    movable=False,
                    brush=pg.mkBrush(96, 125, 139, 55),
                    pen=pg.mkPen(None),
                )
                region.setZValue(-5)
                plot.addItem(region)

    def _clear_cursor_lines(self) -> None:
        for plot, cursor in self._cursor_lines:
            plot.removeItem(cursor)
        self._cursor_lines.clear()

    @staticmethod
    def _event_short_label(event) -> str:
        kind = KIND_NAMES[event.kind]
        if event.kind in {ConfirmationKind.STAIR_SECOND_STEP.value, ConfirmationKind.TRIAL_END.value}:
            return kind
        return f"{kind}  {state_name(event.activity, event.terrain)}"

    def _event_tooltip(self, event) -> str:
        assert self.trial is not None
        lines = [
            self.trial.timestamp(event.sample_index),
            f"{KIND_NAMES[event.kind]}：{state_name(event.activity, event.terrain)}",
            f"来源：{SOURCE_NAMES[event.provenance]}",
        ]
        if event.user_note:
            lines.append(f"备注：{event.user_note}")
        return "\n".join(lines)

    def _populate_main_annotations(self) -> None:
        for item in self._main_annotation_items:
            self.leg_plot.removeItem(item)
        self._main_annotation_items.clear()
        self.annotation_plot.clear()
        if self.trial is None or self.document is None:
            return

        trial, document = self.trial, self.document
        for start_index, end_index, activity, terrain in document.intervals():
            color = QtGui.QColor(STATE_COLORS.get((activity, terrain), "#607D8B"))
            color.setAlpha(38)
            region = pg.LinearRegionItem(
                values=(float(trial.seconds[start_index]), float(trial.seconds[end_index])),
                orientation="vertical",
                movable=False,
                brush=QtGui.QBrush(color),
                pen=pg.mkPen(None),
            )
            region.setAcceptedMouseButtons(QtCore.Qt.MouseButton.NoButton)
            region.setZValue(-20)
            self.leg_plot.addItem(region)
            self._main_annotation_items.append(region)

        for position, event in enumerate(document.composed_events()):
            seconds = float(trial.seconds[event.sample_index])
            component_id = event.component_ids[0]
            selected = event.id == self.selected_event_id
            confirmation = event.kind in {
                ConfirmationKind.STAIR_SECOND_STEP.value,
                ConfirmationKind.TRIAL_END.value,
            }
            color = "#a3312b" if selected else "#397779" if confirmation else "#666c6f"
            line = pg.InfiniteLine(
                pos=seconds,
                angle=90,
                movable=False,
                pen=pg.mkPen(color, width=1.6 if selected else 0.8, style=QtCore.Qt.PenStyle.DashLine if confirmation else QtCore.Qt.PenStyle.SolidLine),
                hoverPen=pg.mkPen("#a3312b", width=2),
            )
            line.setToolTip(self._event_tooltip(event))
            line.setZValue(15)
            line.sigClicked.connect(partial(self._annotation_clicked, component_id))
            self.leg_plot.addItem(line)
            self._main_annotation_items.append(line)

            lane_y = 2.5 - position % 3
            connector = pg.PlotDataItem(
                [seconds, seconds],
                [0.02, lane_y - 0.18],
                pen=pg.mkPen(color, width=0.75),
            )
            if event.sample_index == 0:
                anchor = (0, 0.5)
            elif event.sample_index == len(trial.seconds) - 1:
                anchor = (1, 0.5)
            else:
                anchor = (0.5, 0.5)
            label = EventLabel(
                component_id,
                text=self._event_short_label(event),
                color="#22282b",
                anchor=anchor,
                border=pg.mkPen(color, width=1 if selected else 0.7),
                fill=pg.mkBrush("#fffdf9" if selected else "#ffffff"),
                ensureInBounds=False,
            )
            label.setToolTip(self._event_tooltip(event))
            label.setPos(seconds, lane_y)
            label.clicked.connect(self._annotation_clicked)
            self.annotation_plot.addItem(connector)
            self.annotation_plot.addItem(label)
        self.annotation_plot.setYRange(0, 3, padding=0)

    def _populate_state_plot(self) -> None:
        self._clear_cursor_lines()
        self.state_plot.clear()
        self._markers.clear()
        self._state_items.clear()
        if self.trial is None or self.document is None:
            self._populate_main_annotations()
            return
        trial, document = self.trial, self.document
        self._populate_main_annotations()
        duration = max(trial.duration, 1e-9)
        terrain_colors = {"LEVEL": "#78909C", "ASCENT": "#F57C00", "DESCENT": "#1976D2"}
        for start_index, end_index, activity, terrain in document.intervals():
            start, end = float(trial.seconds[start_index]), float(trial.seconds[end_index])
            width = max(end - start, duration / max(len(trial.seconds), 1))
            for y, color in ((1.05, STATE_COLORS.get((activity, terrain), "#607D8B")), (0.05, terrain_colors[terrain])):
                rectangle = QtWidgets.QGraphicsRectItem(start, y, width, 0.8)
                brush = QtGui.QColor(color)
                brush.setAlpha(105)
                rectangle.setBrush(QtGui.QBrush(brush))
                rectangle.setPen(QtGui.QPen(QtCore.Qt.PenStyle.NoPen))
                rectangle.setZValue(-10)
                self.state_plot.addItem(rectangle)
                self._state_items.append(rectangle)
            if end - start > duration * 0.075:
                activity_text = pg.TextItem(ACTIVITY_NAMES.get(activity, activity), color="#263238", anchor=(0, 0.5))
                activity_text.setPos(start + min(0.25, (end - start) * 0.04), 1.45)
                activity_text.setZValue(-2)
                self.state_plot.addItem(activity_text)
                self._state_items.append(activity_text)
        provenance_style = {
            Provenance.AUTO: QtCore.Qt.PenStyle.SolidLine,
            Provenance.ADJUSTED: QtCore.Qt.PenStyle.DashLine,
            Provenance.MANUAL: QtCore.Qt.PenStyle.DotLine,
            Provenance.IMPORTED_UNKNOWN: QtCore.Qt.PenStyle.DashDotLine,
        }
        for boundary in document.boundaries:
            color = "#6A1B9A" if boundary.track is Track.ACTIVITY else "#00695C"
            selected = boundary.id == self.selected_component_id
            line = pg.InfiniteLine(
                pos=float(trial.seconds[boundary.sample_index]),
                angle=90,
                movable=boundary.sample_index != 0,
                pen=pg.mkPen(color, width=3 if selected else 2, style=provenance_style[boundary.provenance]),
                hoverPen=pg.mkPen("#C62828", width=4),
                span=(0.35, 0.64) if boundary.track is Track.ACTIVITY else (0.02, 0.30),
            )
            line.setZValue(20)
            line.sigClicked.connect(partial(self._marker_clicked, boundary.id))
            line.sigPositionChangeFinished.connect(partial(self._marker_moved, boundary.id, line))
            self.state_plot.addItem(line)
            self._markers[boundary.id] = line
        for confirmation in document.confirmations:
            selected = confirmation.id == self.selected_component_id
            line = pg.InfiniteLine(
                pos=float(trial.seconds[confirmation.sample_index]),
                angle=90,
                movable=True,
                pen=pg.mkPen("#00838F", width=3 if selected else 2, style=QtCore.Qt.PenStyle.DashLine),
                hoverPen=pg.mkPen("#C62828", width=4),
                span=(0.70, 0.98),
            )
            line.setZValue(20)
            line.sigClicked.connect(partial(self._marker_clicked, confirmation.id))
            line.sigPositionChangeFinished.connect(partial(self._marker_moved, confirmation.id, line))
            self.state_plot.addItem(line)
            self._markers[confirmation.id] = line
        for plot in (self.leg_plot, self.pitch_plot, self.motion_plot, self.state_plot):
            cursor = pg.InfiniteLine(
                pos=float(trial.seconds[self.cursor_index]),
                angle=90,
                movable=True,
                bounds=(float(trial.seconds[0]), float(trial.seconds[-1])),
                pen=pg.mkPen("#303638", width=1.2),
                hoverPen=pg.mkPen("#a3312b", width=2.2),
            )
            cursor.setZValue(30)
            cursor.sigPositionChanged.connect(partial(self._cursor_dragged, cursor))
            plot.addItem(cursor)
            self._cursor_lines.append((plot, cursor))
        self.state_plot.setYRange(0, 3, padding=0)
        self.state_plot.setXLink(self.leg_plot)

    def _overview_region_changed(self) -> None:
        if self._syncing_range:
            return
        self._syncing_range = True
        self.leg_plot.setXRange(*self.overview_region.getRegion(), padding=0)
        self._syncing_range = False

    def _detail_range_changed(self, _plot, ranges) -> None:
        if self._syncing_range or not hasattr(self, "overview_region"):
            return
        self._syncing_range = True
        self.overview_region.setRegion(ranges)
        self._syncing_range = False

    def _plot_clicked(self, event) -> None:
        if self.trial is None or event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        point = event.scenePos()
        for plot in (self.leg_plot, self.pitch_plot, self.motion_plot, self.state_plot):
            if plot.sceneBoundingRect().contains(point):
                seconds = float(plot.vb.mapSceneToView(point).x())
                self._set_cursor(self.trial.nearest_index(seconds))
                return

    def _set_cursor(self, index: int) -> None:
        if self.trial is None:
            return
        self.cursor_index = max(0, min(len(self.trial.seconds) - 1, int(index)))
        seconds = float(self.trial.seconds[self.cursor_index])
        self._syncing_cursor = True
        try:
            for _plot, cursor in self._cursor_lines:
                cursor.setValue(seconds)
        finally:
            self._syncing_cursor = False
        self.cursor_label.setText(f"{seconds:.3f} s  |  {self.trial.timestamp(self.cursor_index)}")

    def _cursor_dragged(self, cursor: pg.InfiniteLine, *_args) -> None:
        if self._syncing_cursor or self.trial is None:
            return
        self._set_cursor(self.trial.nearest_index(float(cursor.value())))

    def _toggle_raw(self, visible: bool) -> None:
        for curve in self._raw_curves:
            curve.setVisible(visible)

    def _refresh(self, select_component: str | None = None, select_event: str | None = None) -> None:
        self._refreshing = True
        try:
            self._set_session_enabled(self.trial is not None and self.document is not None and not self._busy)
            if self.trial is None:
                return
            self.time_spin.setRange(0, self.trial.duration)
            self._set_cursor(self.cursor_index)
            if self.document is None:
                self.event_table.setRowCount(0)
                return
            events = self.document.composed_events()
            if select_component:
                match = next((event for event in events if select_component in event.component_ids), None)
                select_event = match.id if match else None
            select_event = select_event or self.selected_event_id
            if not any(event.id == select_event for event in events):
                select_event = events[0].id if events else None
            self.selected_event_id = select_event
            self.selected_component_id = select_component or self.selected_component_id
            self._populate_state_plot()
            self._populate_event_table(events)
            selected = next((event for event in events if event.id == select_event), None)
            self._show_event(selected)
            self._populate_qa()
            self._update_review()
            self._update_status()
        finally:
            self._refreshing = False

    def _populate_event_table(self, events) -> None:
        assert self.trial is not None
        self.event_table.blockSignals(True)
        self.event_table.setRowCount(len(events))
        selected_row = -1
        for row, event in enumerate(events):
            values = (
                self.trial.timestamp(event.sample_index),
                KIND_NAMES[event.kind],
                ACTIVITY_NAMES.get(event.activity, event.activity),
                TERRAIN_NAMES.get(event.terrain, event.terrain),
                SOURCE_NAMES[event.provenance],
                event.user_note,
            )
            for column, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                if column == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, event.id)
                self.event_table.setItem(row, column, item)
            if event.id == self.selected_event_id:
                selected_row = row
        if selected_row >= 0:
            self.event_table.selectRow(selected_row)
        self.event_table.blockSignals(False)

    def _table_selection_changed(self, row: int, _column: int, _old_row: int, _old_column: int) -> None:
        if self._refreshing or row < 0 or self.document is None:
            return
        item = self.event_table.item(row, 0)
        event_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        event = next(value for value in self.document.composed_events() if value.id == event_id)
        self.selected_event_id = event.id
        self.selected_component_id = event.component_ids[0]
        self._set_cursor(event.sample_index)
        self._refresh(select_component=self.selected_component_id)

    def _show_event(self, event) -> None:
        self.component_combo.blockSignals(True)
        self.value_combo.blockSignals(True)
        self.component_combo.clear()
        self.value_combo.clear()
        if event is None or self.trial is None or self.document is None:
            self.timestamp_label.setText("-")
            self.component_combo.blockSignals(False)
            self.value_combo.blockSignals(False)
            return
        self.timestamp_label.setText(self.trial.timestamp(event.sample_index))
        self.time_spin.setValue(float(self.trial.seconds[event.sample_index]))
        components = [item for item in [*self.document.boundaries, *self.document.confirmations] if item.id in event.component_ids]
        labels = {Track.ACTIVITY: "活动边界", Track.TERRAIN: "地形边界"}
        for component in components:
            if isinstance(component, Boundary):
                label = labels[component.track]
            else:
                label = "第二步确认" if component.kind is ConfirmationKind.STAIR_SECOND_STEP else "收尾确认"
            self.component_combo.addItem(label, component.id)
        preferred = self.selected_component_id if self.selected_component_id in event.component_ids else event.component_ids[0]
        index = self.component_combo.findData(preferred)
        self.component_combo.setCurrentIndex(max(0, index))
        self.selected_component_id = preferred
        self.component_combo.blockSignals(False)
        self.value_combo.blockSignals(False)
        self._show_component()

    def _component(self) -> Boundary | Confirmation | None:
        if self.document is None or self.selected_component_id is None:
            return None
        return next(
            (item for item in [*self.document.boundaries, *self.document.confirmations] if item.id == self.selected_component_id),
            None,
        )

    def _component_changed(self) -> None:
        if self._refreshing:
            return
        self.selected_component_id = self.component_combo.currentData()
        self._show_component()
        self._populate_state_plot()

    def _show_component(self) -> None:
        component = self._component()
        self.value_combo.blockSignals(True)
        self.value_combo.clear()
        if component is None:
            self.value_combo.blockSignals(False)
            return
        if isinstance(component, Boundary):
            labels = ACTIVITY_NAMES if component.track is Track.ACTIVITY else TERRAIN_NAMES
            for value, display in labels.items():
                self.value_combo.addItem(display, value)
            self.value_combo.setCurrentIndex(self.value_combo.findData(component.value))
            self.value_combo.setEnabled(True)
        else:
            self.value_combo.addItem("确认标记")
            self.value_combo.setEnabled(False)
        self.note_edit.setText(component.user_note)
        signals = component.evidence.get("signals", {})
        self.evidence_label.setText(
            "  ".join(f"{name} {value:.3f}" for name, value in signals.items()) or "无自动证据记录"
        )
        self.time_spin.setEnabled(component.sample_index != 0)
        self.previous_sample_button.setEnabled(component.sample_index > 0)
        self.next_sample_button.setEnabled(self.trial is not None and component.sample_index < len(self.trial.seconds) - 1)
        self.delete_button.setEnabled(component.sample_index != 0)
        self.value_combo.blockSignals(False)

    def _marker_clicked(self, component_id: str, *_args) -> None:
        self.selected_component_id = component_id
        self._refresh(select_component=component_id)

    def _annotation_clicked(self, component_id: str, *_args) -> None:
        if self.document is None:
            return
        component = next(
            item for item in [*self.document.boundaries, *self.document.confirmations] if item.id == component_id
        )
        self._set_cursor(component.sample_index)
        self._marker_clicked(component_id)

    def _marker_moved(self, component_id: str, line: pg.InfiniteLine, *_args) -> None:
        if self.trial is None or self.document is None:
            return
        component = next(item for item in [*self.document.boundaries, *self.document.confirmations] if item.id == component_id)
        index = self.trial.nearest_index(float(line.value()))
        try:
            if isinstance(component, Boundary):
                self.document.move_boundary(component.id, index)
            else:
                event = next(event for event in self.document.composed_events() if event.id == component.id)
                self.document.move_event(event, index)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法移动", str(exc))
        self._refresh(select_component=component_id)

    def _selected_event(self):
        if self.document is None:
            return None
        return next((event for event in self.document.composed_events() if event.id == self.selected_event_id), None)

    def _time_edited(self) -> None:
        if self._refreshing or self.trial is None or self.document is None:
            return
        event = self._selected_event()
        if event is None:
            return
        index = self.trial.nearest_index(self.time_spin.value())
        try:
            self.document.move_event(event, index)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法移动", str(exc))
        self._refresh(select_component=self.selected_component_id)

    def _step_selected(self, direction: int) -> None:
        if self.document is None or self.trial is None:
            return
        event = self._selected_event()
        if event is None:
            return
        index = max(0, min(len(self.trial.seconds) - 1, event.sample_index + direction))
        try:
            self.document.move_event(event, index)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法移动", str(exc))
        self._refresh(select_component=self.selected_component_id)

    def _value_edited(self) -> None:
        component = self._component()
        if not isinstance(component, Boundary) or self.document is None:
            return
        value = self.value_combo.currentData()
        if value == component.value:
            return
        try:
            self.document.set_boundary_value(component.id, value)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法修改标签", str(exc))
        self._refresh(select_component=component.id)

    def _note_edited(self) -> None:
        component = self._component()
        if component is None or self.document is None or component.user_note == self.note_edit.text():
            return
        try:
            self.document.set_note(component.id, self.note_edit.text())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "备注无效", str(exc))
        self._refresh(select_component=component.id)

    def _add_boundary(self, track: Track) -> None:
        if self.document is None:
            return
        if any(item.track is track and item.sample_index == self.cursor_index for item in self.document.boundaries):
            QtWidgets.QMessageBox.warning(self, "无法添加", "该采样点已经存在同轨边界")
            return
        combo = self.new_activity_combo if track is Track.ACTIVITY else self.new_terrain_combo
        try:
            item = self.document.add_boundary(track, self.cursor_index, combo.currentData())
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法添加", str(exc))
            return
        self._refresh(select_component=item.id)

    def _add_confirmation(self) -> None:
        if self.document is None:
            return
        kind = self.confirmation_combo.currentData()
        try:
            item = self.document.add_confirmation(kind, self.cursor_index)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法添加", str(exc))
            return
        self._refresh(select_component=item.id)

    def _update_interval_values(self) -> None:
        combo = self.interval_value_combo
        current = combo.currentData()
        combo.clear()
        labels = ACTIVITY_NAMES if self.interval_track_combo.currentData() is Track.ACTIVITY else TERRAIN_NAMES
        for value, display in labels.items():
            combo.addItem(display, value)
        index = combo.findData(current)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _set_interval_edge(self, start: bool) -> None:
        if start:
            self.interval_start = self.cursor_index
        else:
            self.interval_end = self.cursor_index
        self._update_interval_label()

    def _update_interval_label(self) -> None:
        if self.trial is None or self.interval_start is None or self.interval_end is None:
            self.interval_range_label.setText("未设置")
            return
        a, b = sorted((self.interval_start, self.interval_end))
        self.interval_range_label.setText(f"{self.trial.seconds[a]:.3f} s -> {self.trial.seconds[b]:.3f} s")

    def _add_interval(self) -> None:
        if self.document is None or self.interval_start is None or self.interval_end is None:
            QtWidgets.QMessageBox.warning(self, "无法添加", "必须先设置区间开始与结束")
            return
        start, end = sorted((self.interval_start, self.interval_end))
        try:
            self.document.add_interval(
                self.interval_track_combo.currentData(),
                start,
                end,
                self.interval_value_combo.currentData(),
            )
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法添加", str(exc))
            return
        self.interval_start = self.interval_end = None
        self._update_interval_label()
        self._refresh()

    def delete_selected(self) -> None:
        component = self._component()
        if component is None or self.document is None:
            return
        reason, accepted = QtWidgets.QInputDialog.getText(self, "删除事件分量", "删除理由：")
        if not accepted:
            return
        try:
            if isinstance(component, Boundary):
                self.document.delete_boundary(component.id, reason)
            else:
                self.document.delete_confirmation(component.id, reason)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.selected_component_id = None
        self.selected_event_id = None
        self._refresh()

    def _populate_qa(self) -> None:
        assert self.document is not None
        current = self.qa_list.currentItem().data(QtCore.Qt.ItemDataRole.UserRole) if self.qa_list.currentItem() else None
        self.qa_list.clear()
        for issue in self.document.issues:
            prefix = "[已闭环]" if issue.resolved else "[阻断]" if issue.severity is Severity.BLOCKING else "[提示]"
            item = QtWidgets.QListWidgetItem(f"{prefix} {issue.message}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, issue.id)
            if issue.resolved:
                item.setForeground(QtGui.QColor("#2E7D32"))
            elif issue.severity is Severity.BLOCKING:
                item.setForeground(QtGui.QColor("#B71C1C"))
            self.qa_list.addItem(item)
            if issue.id == current:
                self.qa_list.setCurrentItem(item)
        has_selection = self.qa_list.currentItem() is not None
        self.qa_fixed_button.setEnabled(has_selection)
        self.qa_accept_button.setEnabled(has_selection)

    def _qa_selection_changed(self, current, _previous) -> None:
        enabled = current is not None
        self.qa_fixed_button.setEnabled(enabled)
        self.qa_accept_button.setEnabled(enabled)

    def _resolve_qa(self, resolution: str) -> None:
        if self.document is None or self.qa_list.currentItem() is None:
            return
        issue_id = self.qa_list.currentItem().data(QtCore.Qt.ItemDataRole.UserRole)
        reason = ""
        if resolution == "accepted":
            reason, accepted = QtWidgets.QInputDialog.getText(self, "确认无需修改", "判断理由：")
            if not accepted:
                return
        try:
            self.document.resolve_issue(issue_id, resolution, reason)
        except ValueError as exc:
            QtWidgets.QMessageBox.warning(self, "无法闭环 QA", str(exc))
        self._refresh()

    def _update_review(self) -> None:
        assert self.document is not None
        errors = self.document.structural_errors()
        unresolved = [issue for issue in self.document.issues if not issue.resolved]
        self.review_checkbox.blockSignals(True)
        self.review_checkbox.setChecked(self.document.reviewed is not None)
        self.review_checkbox.setEnabled(not errors and not unresolved)
        if self.document.reviewed:
            self.annotator_edit.setText(self.document.reviewed["annotator_id"])
        self.review_checkbox.blockSignals(False)
        if errors:
            self.validation_label.setText("；".join(errors))
            self.validation_label.setStyleSheet("color: #b71c1c;")
        elif unresolved:
            self.validation_label.setText(f"尚有 {len(unresolved)} 个 QA 复核项未闭环")
            self.validation_label.setStyleSheet("color: #b71c1c;")
        elif self.document.reviewed is None:
            self.validation_label.setText("等待完整时间线复核声明")
            self.validation_label.setStyleSheet("color: #455a64;")
        else:
            self.validation_label.setText("结构与复核条件已满足")
            self.validation_label.setStyleSheet("color: #2e7d32;")

    def _review_toggled(self, checked: bool) -> None:
        if self._refreshing or self.document is None:
            return
        try:
            if checked:
                annotator = self.annotator_edit.text().strip()
                self.document.attest(annotator, datetime.now(timezone.utc).isoformat())
                self.settings.setValue("annotator_id", annotator)
            else:
                self.document.clear_attestation()
        except ValueError as exc:
            self.review_checkbox.blockSignals(True)
            self.review_checkbox.setChecked(False)
            self.review_checkbox.blockSignals(False)
            QtWidgets.QMessageBox.warning(self, "无法声明复核完成", str(exc))
        self._refresh()

    def _update_status(self) -> None:
        enabled = self.trial is not None and self.document is not None and not self._busy
        self._set_session_enabled(enabled)
        if not enabled:
            self.save_action.setEnabled(False)
            return
        assert self.document is not None
        errors = self.document.structural_errors()
        unresolved = sum(not issue.resolved for issue in self.document.issues)
        if errors:
            text, color = f"结构错误 {len(errors)}", "#B71C1C"
        elif unresolved:
            text, color = f"待复核 {unresolved}", "#E65100"
        elif self.document.reviewed is None:
            text, color = "待完整复核", "#455A64"
        elif self.document.dirty:
            text, color = "可正式保存", "#2E7D32"
        else:
            text, color = "已正式保存", "#1565C0"
        self.review_badge.setText(text)
        self.review_badge.setStyleSheet(f"color: {color}; border-color: {color};")
        self.save_action.setEnabled(self.repository is not None and self.document.ready_to_save)
        self.undo_action.setEnabled(bool(self.document._undo))
        self.redo_action.setEnabled(bool(self.document._redo))
        self.delete_action.setEnabled(self._component() is not None)

    def undo(self) -> None:
        if self.document and self.document.undo():
            self._refresh(select_component=self.selected_component_id)

    def redo(self) -> None:
        if self.document and self.document.redo():
            self._refresh(select_component=self.selected_component_id)

    def rerun_recognition(self) -> None:
        if self.trial is None or self.document is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "重新自动识别",
            "重新识别将整稿替换当前草稿，并作为一次操作进入撤销历史。是否继续？",
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        trial = self.trial

        def finished(result: object) -> None:
            if isinstance(result, AnnotationDocument) and self.document is not None:
                self.document.replace_with(result)
                self._refresh()

        self._run_task("正在重新自动识别...", lambda: recognize(trial), finished)

    def save_formal(self) -> bool:
        if self.document is None or self.repository is None:
            return False
        try:
            self.repository.save(self.document)
        except AnnotationStorageError as exc:
            QtWidgets.QMessageBox.critical(self, "正式保存失败", str(exc))
            return False
        if self.recovery:
            self.recovery.clear()
        self._refresh()
        self.statusBar().showMessage(f"已正式保存：{self.repository.target}", 6000)
        return True

    def export_png(self) -> None:
        if self.trial is None or self.document is None:
            return
        suffix = ".png" if self.document.ready_to_save and not self.document.dirty else ".draft.png"
        initial = self.trial.path.parent / "plots" / f"{self.trial.path.stem}{suffix}"
        name, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出标注图", str(initial), "PNG 图像 (*.png)")
        if not name:
            return
        try:
            output = render_annotation_png(self.trial, self.document, Path(name))
        except OSError as exc:
            QtWidgets.QMessageBox.critical(self, "导出失败", str(exc))
            return
        self.statusBar().showMessage(f"已导出：{output}", 6000)

    def _autosave_recovery(self) -> None:
        if self.document is None or self.recovery is None or self.repository is None or not self.document.dirty:
            return
        try:
            self.recovery.save(
                self.document,
                self.repository.expected_target_hash,
                self.repository.expected_report_hash,
            )
        except OSError as exc:
            self.statusBar().showMessage(f"恢复草稿写入失败：{exc}", 8000)

    def _confirm_leave(self) -> bool:
        if self.document is None or not self.document.dirty:
            return True
        dialog = QtWidgets.QMessageBox(self)
        dialog.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        dialog.setWindowTitle("当前草稿尚未正式保存")
        dialog.setText("当前试次有未正式保存的修改。")
        save_button = None
        if self.document.ready_to_save and self.repository is not None:
            save_button = dialog.addButton("正式保存", QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        discard_button = dialog.addButton("舍弃草稿", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        dialog.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        dialog.exec()
        if save_button is not None and dialog.clickedButton() is save_button:
            return self.save_formal()
        if dialog.clickedButton() is discard_button:
            if self.recovery:
                self.recovery.clear()
            return True
        return False

    def _dispose_session(self) -> None:
        if self.repository:
            self.repository.close()
        self.repository = None
        self.recovery = None
        self.trial = None
        self.document = None
        self.selected_event_id = None
        self.selected_component_id = None
        self.cursor_index = 0
        self.source_label.setText("未打开试次")
        self.target_label.setText("标注文件：未选择")
        self.review_badge.setText("未载入")
        self.event_table.setRowCount(0)
        self._set_session_enabled(False)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if not self._confirm_leave():
            event.ignore()
            return
        self._dispose_session()
        event.accept()

    def _about(self) -> None:
        QtWidgets.QMessageBox.about(
            self,
            f"关于 {APP_NAME}",
            f"{APP_NAME} {VERSION}\nLinux 本地离线版",
        )
