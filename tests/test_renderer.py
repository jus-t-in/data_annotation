from __future__ import annotations

from datetime import datetime
from pathlib import Path

import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import numpy as np
import pytest
from PIL import Image

from youbu_annotation.label_schema import LabelDefinition, LabelSchema, StateDefinition, Track
from youbu_annotation.model import (
    AnnotationDocument,
    Boundary,
    Confirmation,
    ConfirmationKind,
    Provenance,
)
from youbu_annotation.renderer import _build_annotation_figure, render_annotation_png
from youbu_annotation.trial import DataGap, TrialData


def make_schema() -> LabelSchema:
    return LabelSchema(
        schema_id="renderer-test",
        version=1,
        activity_labels=(
            LabelDefinition("STILL", "休止态", "#AAAAAA"),
            LabelDefinition("WALKING", "迈步态", "#BBBBBB"),
        ),
        terrain_labels=(
            LabelDefinition("LEVEL", "测试地面", "#CCCCCC"),
            LabelDefinition("ASCENT", "测试上行", "#DDDDDD"),
        ),
        states=(
            StateDefinition("STILL", "LEVEL", "#123456"),
            StateDefinition("WALKING", "LEVEL", "#2A9D8F"),
            StateDefinition("WALKING", "ASCENT", "#E9C46A"),
        ),
    )


def make_trial(*, absolute: bool = False) -> TrialData:
    seconds = np.arange(9, dtype=float)
    smooth = np.full_like(seconds, -99.0)
    zero = np.zeros_like(seconds)
    start = datetime(2026, 8, 3, 14, 5, 6) if absolute else None
    return TrialData(
        path=Path("P01_S01_T05_v2.csv"),
        session_id="P01_S01",
        trial_id="T05",
        time_column="RX Date/Time" if absolute else "Elapsed (s)",
        seconds=seconds,
        timestamps=[f"{value:.1f}" for value in seconds],
        start_datetime=start,
        channels={
            "display/left_raw": seconds + 10.0,
            "display/right_raw": -(seconds + 20.0),
            "display/left": smooth,
            "display/right": smooth,
            "display/pitch": zero,
            "display/motion": zero,
            "display/impact": zero,
        },
        ignored_columns=(),
        duplicate_rows=0,
        gaps=[DataGap(4, 5, 4.0, 5.0)],
        warnings=[],
        source_hash="renderer-fixture",
    )


def make_document(trial: TrialData, *, note: str = "") -> AnnotationDocument:
    return AnnotationDocument(
        trial,
        [
            Boundary(Track.ACTIVITY, 0, "STILL", Provenance.AUTO, note),
            Boundary(Track.TERRAIN, 0, "LEVEL", Provenance.AUTO, note),
            Boundary(Track.ACTIVITY, 2, "WALKING", Provenance.AUTO, "进入步态"),
            Boundary(Track.TERRAIN, 3, "ASCENT", Provenance.ADJUSTED),
            Boundary(Track.TERRAIN, 6, "LEVEL", Provenance.ADJUSTED),
            Boundary(Track.ACTIVITY, 7, "STILL", Provenance.ADJUSTED),
        ],
        [
            Confirmation(ConfirmationKind.STAIR_SECOND_STEP, 4, Provenance.AUTO, "第二只脚确认"),
            Confirmation(ConfirmationKind.TRIAL_END, 8, Provenance.AUTO),
        ],
        label_schema=make_schema(),
    )


def close_figure(figure) -> None:
    import matplotlib.pyplot as plt

    plt.close(figure)


def test_export_uses_two_axes_raw_leg_data_schema_and_gap() -> None:
    trial = make_trial()
    figure = _build_annotation_figure(trial, make_document(trial), draft=False)
    try:
        assert len(figure.axes) == 2
        note_axis, leg_axis = figure.axes
        np.testing.assert_array_equal(leg_axis.lines[0].get_ydata(), trial.channels["display/left_raw"])
        np.testing.assert_array_equal(leg_axis.lines[1].get_ydata(), trial.channels["display/right_raw"])
        assert note_axis.get_ylabel() == "标注备注"
        assert leg_axis.get_ylabel() == "腿部位置"

        colors = {mcolors.to_hex(patch.get_facecolor()) for patch in leg_axis.patches}
        assert {"#123456", "#2a9d8f", "#e9c46a"} <= colors
        assert any(patch.get_hatch() == "///" for patch in leg_axis.patches)

        labels = {text.get_text() for text in figure.legends[0].get_texts()}
        assert {"休止态·测试地面", "迈步态·测试地面", "迈步态·测试上行"} <= labels
    finally:
        close_figure(figure)


def test_event_notes_always_include_kind_state_and_use_two_line_styles() -> None:
    trial = make_trial()
    figure = _build_annotation_figure(trial, make_document(trial), draft=False)
    try:
        note_axis, leg_axis = figure.axes
        texts = [annotation.get_text() for annotation in note_axis.texts]
        assert texts[0] == "起始：休止态·测试地面"
        assert "边界：迈步态·测试地面\n进入步态" in texts
        assert "第二步确认：迈步态·测试上行\n第二只脚确认" in texts
        assert "收尾确认：休止态·测试地面" in texts

        event_lines = leg_axis.lines[2:]
        solid = [line for line in event_lines if line.get_linestyle() == "-"]
        dashed = [line for line in event_lines if line.get_linestyle() == "--"]
        assert len(solid) == 5
        assert len(dashed) == 2
        assert {mcolors.to_hex(line.get_color()) for line in solid} == {"#616161"}
        assert {mcolors.to_hex(line.get_color()) for line in dashed} == {"#00838f"}
    finally:
        close_figure(figure)


def test_relative_and_absolute_time_axes() -> None:
    relative = make_trial()
    relative_figure = _build_annotation_figure(relative, make_document(relative), draft=False)
    absolute = make_trial(absolute=True)
    absolute_figure = _build_annotation_figure(absolute, make_document(absolute), draft=False)
    try:
        relative_axis = relative_figure.axes[1]
        absolute_axis = absolute_figure.axes[1]
        assert relative_axis.get_xlabel() == "试次内相对时间 t_seconds（秒）"
        assert absolute_axis.get_xlabel() == "绝对时间（2026-08-03）"
        formatter = absolute_axis.xaxis.get_major_formatter()
        assert isinstance(formatter, mdates.DateFormatter)
        assert formatter.fmt == "%H:%M:%S"
    finally:
        close_figure(relative_figure)
        close_figure(absolute_figure)


def test_500_character_note_is_complete_and_grows_without_overlaps() -> None:
    trial = make_trial()
    short_figure = _build_annotation_figure(trial, make_document(trial), draft=False)
    long_note = "长" * 500
    long_figure = _build_annotation_figure(trial, make_document(trial, note=long_note), draft=False)
    try:
        assert long_figure.get_figheight() > short_figure.get_figheight()
        note_axis = long_figure.axes[0]
        rendered = note_axis.texts[0].get_text()
        assert rendered.replace("\n", "") == f"起始：休止态·测试地面{long_note}"

        long_figure.canvas.draw()
        renderer = long_figure.canvas.get_renderer()
        boxes = [annotation.get_bbox_patch().get_window_extent(renderer) for annotation in note_axis.texts]
        assert all(note_axis.bbox.contains(box.x0, box.y0) and note_axis.bbox.contains(box.x1, box.y1) for box in boxes)
        assert all(not first.overlaps(second) for index, first in enumerate(boxes) for second in boxes[index + 1 :])
    finally:
        close_figure(short_figure)
        close_figure(long_figure)


@pytest.mark.parametrize("draft", [False, True])
def test_title_draft_marker_and_png_output(tmp_path: Path, draft: bool) -> None:
    trial = make_trial()
    document = make_document(trial)
    figure = _build_annotation_figure(trial, document, draft=draft)
    try:
        assert figure.axes[1].get_title() == trial.path.name
        marker = [text for text in figure.texts if text.get_text() == "草稿 / 非正式标注"]
        assert bool(marker) is draft
        assert "步态数据标注" not in "".join(text.get_text() for text in figure.texts)
    finally:
        close_figure(figure)

    output = render_annotation_png(trial, document, tmp_path / "export.png", draft=draft)
    assert output == (tmp_path / "export.png").resolve()
    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.width > image.height > 0
