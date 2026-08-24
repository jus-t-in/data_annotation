"""Static PNG export compatible with the project's existing plot language."""

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .constants import LEFT_COLOR, RIGHT_COLOR
from .model import AnnotationDocument, ComposedEvent, ConfirmationKind
from .trial import TrialData

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Droid Sans Fallback", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

NOTE_WRAP_WIDTH = 24
NOTE_FONT_SIZE = 7.5
MAIN_PLOT_HEIGHT = 5.5
NOTE_WRAPPER = textwrap.TextWrapper(
    width=NOTE_WRAP_WIDTH,
    replace_whitespace=False,
    drop_whitespace=False,
    break_on_hyphens=False,
)


@dataclass(frozen=True)
class _Note:
    event: ComposedEvent
    text: str
    center: float
    lane: int


def _event_text(document: AnnotationDocument, event: ComposedEvent) -> str:
    kind = {
        "initial": "起始",
        "boundary": "边界",
        ConfirmationKind.STAIR_SECOND_STEP.value: "第二步确认",
        ConfirmationKind.TRIAL_END.value: "收尾确认",
    }[event.kind]
    heading = f"{kind}：{document.label_schema.state_name(event.activity, event.terrain)}"
    lines = NOTE_WRAPPER.wrap(heading)
    if event.user_note:
        lines.extend(NOTE_WRAPPER.wrap(event.user_note) or [""])
    return "\n".join(lines)


def _layout_notes(document: AnnotationDocument) -> tuple[list[_Note], list[float]]:
    duration = max(document.trial.duration, 1.0)
    lane_ends: list[float] = []
    lane_lines: list[int] = []
    layout: list[_Note] = []

    for event in document.composed_events():
        text = _event_text(document, event)
        lines = text.splitlines()
        longest = max(map(len, lines))
        width = min(0.2, max(0.08, longest * 0.0062 + 0.01))
        half_width = width / 2
        event_position = float(document.trial.seconds[event.sample_index]) / duration
        center = min(max(event_position, half_width), 1.0 - half_width)
        left, right = center - half_width, center + half_width
        lane = next(
            (index for index, lane_end in enumerate(lane_ends) if left > lane_end + 0.008),
            len(lane_ends),
        )
        if lane == len(lane_ends):
            lane_ends.append(right)
            lane_lines.append(len(lines))
        else:
            lane_ends[lane] = right
            lane_lines[lane] = max(lane_lines[lane], len(lines))
        layout.append(_Note(event, text, center, lane))

    lane_heights = [max(0.55, line_count * 0.145 + 0.18) for line_count in lane_lines]
    return layout, lane_heights or [0.55]


def _build_annotation_figure(
    trial: TrialData,
    document: AnnotationDocument,
    *,
    draft: bool,
):
    note_layout, lane_heights = _layout_notes(document)
    note_height = max(2.0, sum(lane_heights) + 0.3)
    figure = plt.figure(figsize=(18, 6.5 + note_height), constrained_layout=False)
    grid = figure.add_gridspec(2, 1, height_ratios=[note_height, MAIN_PLOT_HEIGHT], hspace=0.02)
    note_axis = figure.add_subplot(grid[0])
    leg_axis = figure.add_subplot(grid[1], sharex=note_axis)
    schema = document.label_schema

    if trial.start_datetime is None:
        x_values = trial.seconds

        def x_at(seconds: float):
            return seconds

    else:
        x_values = [trial.start_datetime + timedelta(seconds=float(value)) for value in trial.seconds]

        def x_at(seconds: float):
            return trial.start_datetime + timedelta(seconds=seconds)

    present_states: list[tuple[str, str]] = []
    for start_index, end_index, activity, terrain in document.intervals():
        state = (activity, terrain)
        leg_axis.axvspan(
            x_values[start_index],
            x_values[end_index],
            color=schema.state_color(*state),
            alpha=0.18,
            zorder=0,
        )
        if state not in present_states:
            present_states.append(state)

    leg_axis.plot(
        x_values,
        trial.channels["display/left_raw"],
        color=LEFT_COLOR,
        linewidth=0.85,
        label="左腿位置",
        zorder=3,
    )
    leg_axis.plot(
        x_values,
        trial.channels["display/right_raw"],
        color=RIGHT_COLOR,
        linewidth=0.85,
        label="右腿位置",
        zorder=3,
    )

    for event in document.composed_events():
        is_confirmation = event.kind in {
            ConfirmationKind.STAIR_SECOND_STEP.value,
            ConfirmationKind.TRIAL_END.value,
        }
        leg_axis.axvline(
            x_at(float(trial.seconds[event.sample_index])),
            color="#00838F" if is_confirmation else "#616161",
            linewidth=0.55,
            alpha=0.55,
            linestyle="--" if is_confirmation else "-",
            zorder=2,
        )

    lane_tops: list[float] = []
    used_height = 0.15
    for height in lane_heights:
        lane_tops.append(note_height - used_height - height / 2)
        used_height += height
    x_start, x_end = x_values[0], x_values[-1]
    for note in note_layout:
        text_x = x_start + (x_end - x_start) * note.center
        note_axis.annotate(
            note.text,
            xy=(x_at(float(trial.seconds[note.event.sample_index])), 0.02),
            xytext=(text_x, lane_tops[note.lane]),
            ha="center",
            va="center",
            fontsize=NOTE_FONT_SIZE,
            linespacing=1.15,
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "edgecolor": "#777777",
                "linewidth": 0.55,
            },
            arrowprops={"arrowstyle": "-", "color": "#555555", "linewidth": 0.65},
        )

    for gap in trial.gaps:
        leg_axis.axvspan(
            x_at(gap.start),
            x_at(gap.end),
            color="#BDBDBD",
            alpha=0.32,
            hatch="///",
            zorder=4,
        )

    note_axis.set_ylim(0, note_height)
    note_axis.set_ylabel("标注备注")
    note_axis.tick_params(axis="x", labelbottom=False, bottom=False)
    note_axis.tick_params(axis="y", left=False, labelleft=False)
    for side in ("top", "right", "left"):
        note_axis.spines[side].set_visible(False)

    if trial.start_datetime is None:
        leg_axis.set_xlabel("试次内相对时间 t_seconds（秒）")
    else:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        leg_axis.xaxis.set_major_locator(locator)
        leg_axis.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        leg_axis.set_xlabel(f"绝对时间（{trial.start_datetime:%Y-%m-%d}）")
    leg_axis.set_xlim(x_start, x_end)
    leg_axis.set_ylabel("腿部位置")
    leg_axis.grid(axis="y", color="#BDBDBD", linewidth=0.55, alpha=0.6)
    leg_axis.set_title(trial.path.name, fontsize=14, pad=10)

    handles: list[Line2D | Patch] = [
        Line2D([0], [0], color=LEFT_COLOR, linewidth=1.5, label="左腿位置"),
        Line2D([0], [0], color=RIGHT_COLOR, linewidth=1.5, label="右腿位置"),
    ]
    handles.extend(
        Patch(
            facecolor=schema.state_color(*state),
            alpha=0.35,
            label=schema.state_name(*state),
        )
        for state in present_states
    )
    legend_columns = min(5, len(handles))
    legend_rows = math.ceil(len(handles) / legend_columns)
    figure.legend(
        handles=handles,
        loc="lower center",
        ncol=legend_columns,
        frameon=False,
        fontsize=9,
    )
    if draft:
        figure.text(
            0.985,
            0.975,
            "草稿 / 非正式标注",
            ha="right",
            va="top",
            color="#C62828",
            fontsize=12,
            weight="bold",
        )
    bottom = min(0.2, (0.52 + 0.2 * legend_rows) / figure.get_figheight())
    figure.subplots_adjust(left=0.065, right=0.985, top=0.97, bottom=bottom)
    return figure


def render_annotation_png(
    trial: TrialData,
    document: AnnotationDocument,
    output: Path,
    *,
    draft: bool | None = None,
) -> Path:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    draft = document.dirty or not document.ready_to_save if draft is None else draft
    figure = _build_annotation_figure(trial, document, draft=draft)
    figure.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(figure)
    return output
