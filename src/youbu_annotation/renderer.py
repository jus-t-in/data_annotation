"""Static PNG export compatible with the project's existing plot language."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from .constants import (
    IMPACT_COLOR,
    LEFT_COLOR,
    MOTION_COLOR,
    PITCH_COLOR,
    RIGHT_COLOR,
    STATE_COLORS,
    state_name,
)
from .model import AnnotationDocument, ConfirmationKind
from .trial import TrialData

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Droid Sans Fallback", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


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
    x = trial.seconds

    fig = plt.figure(figsize=(18, 9), constrained_layout=False)
    grid = fig.add_gridspec(4, 1, height_ratios=[1.25, 4.2, 1.55, 1.55], hspace=0.08)
    note_ax = fig.add_subplot(grid[0])
    leg_ax = fig.add_subplot(grid[1], sharex=note_ax)
    pitch_ax = fig.add_subplot(grid[2], sharex=note_ax)
    motion_ax = fig.add_subplot(grid[3], sharex=note_ax)

    present_states = []
    for start_index, end_index, activity, terrain in document.intervals():
        start = float(x[start_index])
        end = float(x[end_index])
        state = (activity, terrain)
        color = STATE_COLORS.get(state, "#607D8B")
        leg_ax.axvspan(start, end, color=color, alpha=0.18, zorder=0)
        if state not in present_states:
            present_states.append(state)

    leg_ax.plot(x, trial.channels["display/left"], color=LEFT_COLOR, linewidth=0.85, label="左腿位置")
    leg_ax.plot(x, trial.channels["display/right"], color=RIGHT_COLOR, linewidth=0.85, label="右腿位置")
    pitch_ax.plot(x, trial.channels["display/pitch"], color=PITCH_COLOR, linewidth=0.8)
    motion_ax.plot(x, trial.channels["display/motion"], color=MOTION_COLOR, linewidth=0.75, label="运动强度")
    motion_ax.plot(x, trial.channels["display/impact"], color=IMPACT_COLOR, linewidth=0.75, label="冲击")

    events = document.composed_events()
    lane_count = 3
    for position, event in enumerate(events):
        seconds = float(x[event.sample_index])
        is_confirmation = event.kind in {
            ConfirmationKind.STAIR_SECOND_STEP.value,
            ConfirmationKind.TRIAL_END.value,
        }
        for axis in (leg_ax, pitch_ax, motion_ax):
            axis.axvline(
                seconds,
                color="#616161" if not is_confirmation else "#00838F",
                linewidth=0.55,
                alpha=0.5,
                linestyle="--" if is_confirmation else "-",
            )
        kind = {
            "initial": "起始",
            "boundary": "边界",
            ConfirmationKind.STAIR_SECOND_STEP.value: "第二步确认",
            ConfirmationKind.TRIAL_END.value: "收尾确认",
        }[event.kind]
        label = event.user_note or f"{kind}：{state_name(event.activity, event.terrain)}"
        lane = position % lane_count
        note_ax.annotate(
            label,
            xy=(seconds, 0.02),
            xytext=(seconds, lane_count - lane - 0.45),
            ha="center",
            va="center",
            fontsize=7.2,
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "edgecolor": "#777777", "linewidth": 0.5},
            arrowprops={"arrowstyle": "-", "color": "#666666", "linewidth": 0.55},
        )

    for gap in trial.gaps:
        for axis in (leg_ax, pitch_ax, motion_ax):
            axis.axvspan(gap.start, gap.end, color="#BDBDBD", alpha=0.32, hatch="///", zorder=4)

    note_ax.set_ylim(0, lane_count)
    note_ax.set_ylabel("标注")
    note_ax.tick_params(axis="both", left=False, labelleft=False, bottom=False, labelbottom=False)
    for side in ("top", "right", "left"):
        note_ax.spines[side].set_visible(False)

    leg_ax.set_ylabel("腿部位置")
    pitch_ax.set_ylabel("俯仰")
    motion_ax.set_ylabel("强度")
    motion_ax.set_xlabel(
        f"试次内相对时间（秒）  |  起始 {trial.start_datetime:%Y-%m-%d %H:%M:%S}"
        if trial.start_datetime
        else "试次内相对时间（秒）"
    )
    for axis in (leg_ax, pitch_ax, motion_ax):
        axis.grid(axis="y", color="#BDBDBD", linewidth=0.5, alpha=0.55)
        axis.set_xlim(float(x[0]), float(x[-1]))
    motion_ax.legend(loc="upper right", frameon=False, ncol=2, fontsize=8)

    handles = [
        Line2D([0], [0], color=LEFT_COLOR, linewidth=1.5, label="左腿位置"),
        Line2D([0], [0], color=RIGHT_COLOR, linewidth=1.5, label="右腿位置"),
    ]
    handles.extend(
        Patch(facecolor=STATE_COLORS.get(state, "#607D8B"), alpha=0.35, label=state_name(*state))
        for state in present_states
    )
    fig.legend(handles=handles, loc="lower center", ncol=min(6, len(handles)), frameon=False, fontsize=8.5)
    title = f"{trial.path.name}  |  步态数据标注"
    if draft:
        title += "  [草稿]"
        fig.text(0.985, 0.975, "草稿 / 非正式标注", ha="right", va="top", color="#C62828", fontsize=12, weight="bold")
    fig.suptitle(title, fontsize=14, y=0.985)
    fig.subplots_adjust(left=0.065, right=0.985, top=0.94, bottom=0.095)
    fig.savefig(output, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output
