from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

import numpy as np
import pytest
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from youbu_annotation.constants import label_color, state_color
from youbu_annotation.gui import AnnotationEditor, EventLabel
from youbu_annotation.label_schema import DEFAULT_V3_SCHEMA
from youbu_annotation.model import AnnotationDocument, Boundary, Confirmation, ConfirmationKind, Provenance, Track
from youbu_annotation.trial import DataGap, TrialData


@pytest.fixture(scope="session")
def application():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def trial() -> TrialData:
    seconds = np.array([0.0, 0.5, 1.2, 2.0, 3.2, 4.0])
    zero = np.zeros_like(seconds)
    return TrialData(
        path=Path("P01_S01_T01_.csv"),
        session_id="P01_S01",
        trial_id="T01",
        time_column="Elapsed (s)",
        seconds=seconds,
        timestamps=[f"{value:.1f}" for value in seconds],
        start_datetime=None,
        channels={
            "display/left": -np.sin(seconds),
            "display/right": np.sin(seconds),
            "display/pitch": zero,
            "display/gyro_y": np.sin(seconds),
            "display/gyro_y_raw": 2 * np.sin(seconds),
            "display/motion": np.abs(np.sin(seconds)),
            "display/impact": zero,
            "display/left_raw": -np.sin(seconds),
            "display/right_raw": np.sin(seconds),
            "display/pitch_raw": zero,
        },
        ignored_columns=(),
        duplicate_rows=0,
        gaps=[],
        warnings=[],
        source_hash="fixture",
    )


@pytest.fixture
def document(trial: TrialData) -> AnnotationDocument:
    return AnnotationDocument(
        trial,
        [
            Boundary(Track.ACTIVITY, 0, "STILL", Provenance.AUTO),
            Boundary(Track.TERRAIN, 0, "LEVEL", Provenance.AUTO),
            Boundary(Track.ACTIVITY, 2, "WALKING", Provenance.AUTO),
            Boundary(Track.TERRAIN, 3, "ASCENT", Provenance.ADJUSTED),
        ],
        [Confirmation(ConfirmationKind.STAIR_SECOND_STEP, 4, Provenance.AUTO, "相反腿落脚")],
    )


@pytest.fixture
def v3_trial(trial: TrialData) -> TrialData:
    trial.path = Path("P01_S01_T03_v3.csv")
    trial.trial_id = "T03"
    return trial


@pytest.fixture
def v3_document(v3_trial: TrialData) -> AnnotationDocument:
    return AnnotationDocument(
        v3_trial,
        [
            Boundary(Track.ACTIVITY, 0, "STILL", Provenance.AUTO),
            Boundary(Track.TERRAIN, 0, "LEVEL", Provenance.AUTO),
            Boundary(Track.ACTIVITY, 2, "WALKING", Provenance.AUTO),
            Boundary(Track.TERRAIN, 2, "ASCENT", Provenance.AUTO),
        ],
        label_schema=DEFAULT_V3_SCHEMA,
    )


def test_gui_displays_gap_reconfirmation_as_fixed_event(application, v3_trial: TrialData):
    v3_trial.gaps = [DataGap(2, 3, 1.2, 2.0)]
    document = AnnotationDocument.from_rows(v3_trial, [])
    window = AnnotationEditor()
    window.load_session(v3_trial, document)
    window.show()
    application.processEvents()
    try:
        marker = next(
            item for item in document.confirmations
            if item.kind is ConfirmationKind.GAP_RECONFIRMATION
        )
        assert any(
            window.event_table.item(row, 1).text() == "断档后重新确认"
            for row in range(window.event_table.rowCount())
        )
        assert "1.200000–2.000000 s" in window.qa_list.item(0).text()
        window.selected_component_id = marker.id
        window._show_component()
        assert not window.time_spin.isEnabled()
        assert not window.delete_button.isEnabled()
    finally:
        window.close()
        application.processEvents()


@pytest.fixture
def editor(application, tmp_path: Path, trial: TrialData, document: AnnotationDocument):
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    settings = QtCore.QSettings("Youbu", "AnnotationEditor")
    settings.clear()
    settings.sync()
    window = AnnotationEditor()
    window.load_session(trial, document)
    window.show()
    application.processEvents()
    yield window
    window.close()
    application.processEvents()


@pytest.fixture
def v3_editor(application, tmp_path: Path, v3_trial: TrialData, v3_document: AnnotationDocument):
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    settings = QtCore.QSettings("Youbu", "AnnotationEditor")
    settings.clear()
    settings.sync()
    window = AnnotationEditor()
    window.load_session(v3_trial, v3_document)
    window.show()
    application.processEvents()
    yield window
    window.close()
    application.processEvents()


def test_empty_state_transitions_to_loaded_workspace(
    application,
    tmp_path: Path,
    trial: TrialData,
    document: AnnotationDocument,
) -> None:
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat, QtCore.QSettings.Scope.UserScope, str(tmp_path))
    window = AnnotationEditor()
    window.show()
    application.processEvents()
    try:
        assert window.workspace_stack.currentWidget() is window.empty_state
        assert window.empty_open_button.isVisible()
        assert not window.save_action.isEnabled()

        window.load_session(trial, document)
        application.processEvents()

        assert window.workspace_stack.currentWidget() is window.main_splitter
        assert window.event_count_label.text() == f"{len(document.composed_events())} 项"
        assert window.source_label.text().startswith("P01_S01 / T01")
    finally:
        window._dispose_session()
        window.close()


def test_desktop_uses_boundary_creation_only(editor: AnnotationEditor) -> None:
    assert not hasattr(editor, "interval_start")
    assert not hasattr(editor, "interval_end")
    assert not hasattr(editor, "interval_track_combo")
    assert not any(
        button.text() == "添加区间"
        for button in editor.findChildren(QtWidgets.QPushButton)
    )


def test_toolbar_groups_actions_and_emphasizes_save(editor: AnnotationEditor) -> None:
    save_button = editor.toolbar.widgetForAction(editor.save_action)
    assert isinstance(save_button, QtWidgets.QToolButton)
    assert save_button.objectName() == "primaryToolButton"
    assert sum(action.isSeparator() for action in editor.toolbar.actions()) == 3
    for action in (editor.undo_action, editor.redo_action):
        button = editor.toolbar.widgetForAction(action)
        assert isinstance(button, QtWidgets.QToolButton)
        assert button.toolButtonStyle() is QtCore.Qt.ToolButtonStyle.ToolButtonIconOnly


def test_auxiliary_plot_defaults_and_persistence(editor: AnnotationEditor, application) -> None:
    assert editor.auxiliary_actions["overview"].isChecked()
    assert not editor.auxiliary_actions["pitch"].isChecked()
    assert not editor.auxiliary_actions["motion"].isChecked()
    assert editor.overview_plot.isVisible()
    assert not editor.pitch_plot.isVisible()
    assert not editor.motion_plot.isVisible()

    editor.auxiliary_actions["pitch"].setChecked(True)
    application.processEvents()
    editor.settings.sync()

    restored = AnnotationEditor()
    try:
        assert restored.auxiliary_actions["pitch"].isChecked()
        assert restored.pitch_plot.isVisible()
    finally:
        restored.close()


def test_cursor_drag_snaps_without_editing_and_refresh_cleans_old_lines(
    editor: AnnotationEditor,
    application,
) -> None:
    assert editor.document is not None
    old_cursors = list(editor._cursor_lines)
    dirty = editor.document.dirty
    undo_count = len(editor.document._undo)

    old_cursors[0][1].setValue(1.0)
    application.processEvents()

    assert editor.cursor_index == 2
    assert all(cursor.value() == pytest.approx(1.2) for _plot, cursor in editor._cursor_lines)
    assert editor.document.dirty is dirty
    assert len(editor.document._undo) == undo_count

    editor._refresh()
    application.processEvents()
    assert len(editor._cursor_lines) == 4
    assert all(cursor not in plot.items for plot, cursor in old_cursors)


def test_cursor_accepts_press_and_drag(editor: AnnotationEditor, application) -> None:
    editor._set_cursor(1)
    viewport = editor.graphics.viewport()
    start = editor.graphics.mapFromScene(editor.leg_plot.vb.mapViewToScene(QtCore.QPointF(0.5, 0.0)))
    end = editor.graphics.mapFromScene(editor.leg_plot.vb.mapViewToScene(QtCore.QPointF(2.0, 0.0)))

    QtTest.QTest.mousePress(viewport, QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(viewport, end, delay=20)
    QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MouseButton.LeftButton, pos=end)
    application.processEvents()

    assert editor.cursor_index == 3
    assert all(cursor.value() == pytest.approx(2.0) for _plot, cursor in editor._cursor_lines)


def test_main_annotations_select_events(editor: AnnotationEditor, application) -> None:
    assert editor.document is not None
    labels = [item for item in editor.annotation_plot.items if isinstance(item, EventLabel)]
    regions = [item for item in editor._main_annotation_items if isinstance(item, pg.LinearRegionItem)]
    assert len(labels) == len(editor.document.composed_events())
    assert len(regions) == len(editor.document.intervals())
    assert all("来源：" in label.toolTip() for label in labels)
    fixed = [item for item in labels if not item.draggable]
    assert len(fixed) == 1
    assert fixed[0].cursor().shape() is QtCore.Qt.CursorShape.ForbiddenCursor
    assert all(
        item.cursor().shape() is QtCore.Qt.CursorShape.SizeHorCursor
        for item in labels
        if item.draggable
    )

    target = labels[-1]
    component = next(
        item for item in [*editor.document.boundaries, *editor.document.confirmations] if item.id == target.component_id
    )
    target.clicked.emit(target.component_id)
    application.processEvents()

    assert editor.selected_component_id == component.id
    assert editor.cursor_index == component.sample_index


def test_main_annotation_drag_previews_and_commits_one_snapped_move(
    editor: AnnotationEditor,
    application,
) -> None:
    assert editor.document is not None
    callout = next(item for item in editor._event_callouts.values() if item.event.sample_index == 2)
    component = editor.document.find_boundary(callout.event.component_ids[0])
    undo_count = len(editor.document._undo)
    target = editor.annotation_plot.vb.mapViewToScene(QtCore.QPointF(0.55, callout.lane_y))

    callout.label.drag_started.emit(component.id)
    callout.label.dragged.emit(component.id, target)
    application.processEvents()

    assert callout.label.pos().x() == pytest.approx(0.55)
    assert callout.connector.xData.tolist() == pytest.approx([0.55, 0.55])
    assert callout.line.value() == pytest.approx(0.55)
    assert editor._markers[component.id].value() == pytest.approx(0.55)
    assert component.sample_index == 2

    callout.label.drag_finished.emit(component.id, target)
    application.processEvents()

    assert component.sample_index == 1
    assert component.provenance is Provenance.ADJUSTED
    assert len(editor.document._undo) == undo_count + 1
    assert editor.cursor_index == 1
    assert editor.selected_component_id == component.id
    editor.document.dirty = False


def test_main_annotation_accepts_click_and_drag_gestures(
    editor: AnnotationEditor,
    application,
) -> None:
    assert editor.document is not None
    viewport = editor.graphics.viewport()
    callout = next(item for item in editor._event_callouts.values() if item.event.sample_index == 2)
    component = editor.document.find_boundary(callout.event.component_ids[0])
    center = callout.label.mapToScene(callout.label.boundingRect().center())
    start = editor.graphics.mapFromScene(center)

    QtTest.QTest.mouseClick(viewport, QtCore.Qt.MouseButton.LeftButton, pos=start)
    application.processEvents()
    assert editor.selected_component_id == component.id
    assert component.sample_index == 2

    callout = editor._event_callouts[component.id]
    center = callout.label.mapToScene(callout.label.boundingRect().center())
    start = editor.graphics.mapFromScene(center)
    target = editor.annotation_plot.vb.mapViewToScene(QtCore.QPointF(0.5, callout.lane_y))
    end = editor.graphics.mapFromScene(target)
    QtTest.QTest.mousePress(viewport, QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(viewport, end, delay=20)
    QtTest.QTest.mouseRelease(viewport, QtCore.Qt.MouseButton.LeftButton, pos=end)
    application.processEvents()

    assert component.sample_index == 1
    editor.document.dirty = False


def test_v3_terrain_is_default_component_and_all_labels_are_editable(
    v3_editor: AnnotationEditor,
    application,
) -> None:
    assert v3_editor.document is not None
    expected = ["平地", "上楼", "下楼", "上坡", "下坡"]
    assert [
        v3_editor.new_terrain_combo.itemText(index)
        for index in range(v3_editor.new_terrain_combo.count())
    ] == expected

    event = next(item for item in v3_editor.document.composed_events() if item.sample_index == 2)
    row = next(
        index
        for index in range(v3_editor.event_table.rowCount())
        if v3_editor.event_table.item(index, 0).data(QtCore.Qt.ItemDataRole.UserRole) == event.id
    )
    v3_editor.event_table.selectRow(row)
    application.processEvents()

    terrain = next(
        item
        for item in v3_editor.document.boundaries
        if item.track is Track.TERRAIN and item.sample_index == 2
    )
    activity = next(
        item
        for item in v3_editor.document.boundaries
        if item.track is Track.ACTIVITY and item.sample_index == 2
    )
    assert v3_editor.selected_component_id == terrain.id
    assert v3_editor.value_label.text() == "地形标签"
    assert [
        v3_editor.value_combo.itemText(index)
        for index in range(v3_editor.value_combo.count())
    ] == expected

    v3_editor._marker_clicked(activity.id)
    application.processEvents()
    assert v3_editor.selected_component_id == activity.id
    assert v3_editor.value_label.text() == "活动标签"


def test_v3_adding_terrain_boundary_keeps_activity_track_unchanged(
    v3_editor: AnnotationEditor,
    application,
) -> None:
    assert v3_editor.document is not None
    activity_before = [
        (item.sample_index, item.value)
        for item in v3_editor.document.track_boundaries(Track.ACTIVITY)
    ]
    v3_editor._set_cursor(4)
    v3_editor.new_terrain_combo.setCurrentIndex(v3_editor.new_terrain_combo.findData("DECLINE"))
    v3_editor.add_terrain_button.click()
    application.processEvents()

    terrain = next(item for item in v3_editor.document.boundaries if item.value == "DECLINE")
    assert terrain.track is Track.TERRAIN
    assert terrain.sample_index == 4
    assert v3_editor.selected_component_id == terrain.id
    assert v3_editor.value_label.text() == "地形标签"
    assert [
        item.sample_index for item in v3_editor.document.track_boundaries(Track.ACTIVITY)
    ] == [item[0] for item in activity_before]
    assert [
        item.value for item in v3_editor.document.track_boundaries(Track.ACTIVITY)
    ] == [item[1] for item in activity_before]
    assert v3_editor.document.state_at(4) == ("WALKING", "DECLINE")
    v3_editor.document.dirty = False


def test_custom_label_colors_reach_editor_overlays(editor: AnnotationEditor, application) -> None:
    assert editor.document is not None
    editor.catalog.add(Track.TERRAIN, "INCLINE")
    editor.document.add_boundary(Track.TERRAIN, 5, "INCLINE")
    editor._refresh()
    application.processEvents()

    intervals = editor.document.intervals()
    regions = [item for item in editor._main_annotation_items if isinstance(item, pg.LinearRegionItem)]
    assert [region.brush.color().name() for region in regions] == [
        QtGui.QColor(state_color(activity, terrain)).name()
        for _start, _end, activity, terrain in intervals
    ]

    rectangles = [item for item in editor._state_items if isinstance(item, QtWidgets.QGraphicsRectItem)]
    assert len(rectangles) == len(intervals) * 2
    for index, (_start, _end, _activity, terrain) in enumerate(intervals):
        assert rectangles[index * 2].brush().color().name() == regions[index].brush.color().name()
        assert rectangles[index * 2 + 1].brush().color().name() == QtGui.QColor(
            label_color("terrain", terrain)
        ).name()
    editor.document.dirty = False


def test_overview_tracks_detail_range(editor: AnnotationEditor, application) -> None:
    editor.leg_plot.setXRange(0.5, 2.0, padding=0)
    application.processEvents()
    start, end = editor.overview_region.getRegion()
    assert start == pytest.approx(0.5)
    assert end == pytest.approx(2.0)


@pytest.mark.parametrize("size", [(1440, 900), (1100, 680)])
def test_editor_renders_at_supported_sizes(editor: AnnotationEditor, application, size: tuple[int, int]) -> None:
    editor.resize(*size)
    application.processEvents()
    image = editor.grab().toImage()
    assert not image.isNull()
    assert (image.width(), image.height()) == size


def test_main_plot_remains_readable_with_all_auxiliary_plots_at_minimum_size(
    editor: AnnotationEditor,
    application,
) -> None:
    editor.resize(1100, 680)
    editor.auxiliary_actions["pitch"].setChecked(True)
    editor.auxiliary_actions["motion"].setChecked(True)
    application.processEvents()
    assert editor.leg_plot.sceneBoundingRect().height() >= 80
