from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_OPENGL", "software")

import numpy as np
import pytest
import pyqtgraph as pg
from PySide6 import QtCore, QtTest, QtWidgets

from youbu_annotation.gui import AnnotationEditor, EventLabel
from youbu_annotation.model import AnnotationDocument, Boundary, Confirmation, ConfirmationKind, Provenance, Track
from youbu_annotation.trial import TrialData


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

    target = labels[-1]
    component = next(
        item for item in [*editor.document.boundaries, *editor.document.confirmations] if item.id == target.component_id
    )
    target.clicked.emit(target.component_id)
    application.processEvents()

    assert editor.selected_component_id == component.id
    assert editor.cursor_index == component.sample_index


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
