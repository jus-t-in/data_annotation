#!/usr/bin/env python3
"""绘制左右腿位置曲线、组合状态色块和事件备注。"""

from __future__ import annotations

import argparse
import csv
import sys
import textwrap
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
except ImportError:
    raise SystemExit("缺少 matplotlib，请先运行：python3 -m pip install -r requirements.txt")

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Droid Sans Fallback", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False


TIME_FORMAT = "%Y/%m/%d %H:%M:%S::%f"
TIME_COLUMN = "RX Date/Time"
ELAPSED_COLUMN = "Elapsed (s)"    # 7.27 起的导出格式：试次内相对秒
RIGHT_COLUMN = "左右腿位置/右腿位置"
LEFT_COLUMN = "左右腿位置/左右位置"
LEFT_COLUMN_ALT = "左右腿位置/左腿位置"  # 7.27 起的列名；语义同 LEFT_COLUMN

ACTIVITY_NAMES = {
    "STILL": "静止",
    "WALKING": "行走",
    "BEND": "弯腰",
    "SQUAT": "深蹲",
    "HIGH_KNEE_SINGLE": "单腿高抬",
    "HIGH_KNEE_ALTERNATING": "交替高抬腿",
    "TURNING_LEFT": "左转",
    "TURNING_RIGHT": "右转",
    "SHUFFLE": "拖步",
    "OTHER": "协议外动作",
}
TERRAIN_NAMES = {"LEVEL": "平地", "ASCENT": "上楼", "DESCENT": "下楼"}
STATE_COLORS = {
    ("STILL", "LEVEL"): "#9E9E9E",
    ("WALKING", "LEVEL"): "#4CAF50",
    ("WALKING", "ASCENT"): "#FF9800",
    ("WALKING", "DESCENT"): "#2196F3",
    ("STILL", "ASCENT"): "#FFC107",
    ("STILL", "DESCENT"): "#03A9F4",
    ("BEND", "LEVEL"): "#8E44AD",
    ("SQUAT", "LEVEL"): "#E91E63",
    ("HIGH_KNEE_SINGLE", "LEVEL"): "#00ACC1",
    ("HIGH_KNEE_ALTERNATING", "LEVEL"): "#7CB342",
    ("TURNING_LEFT", "LEVEL"): "#5C6BC0",
    ("TURNING_RIGHT", "LEVEL"): "#AB47BC",
    ("SHUFFLE", "LEVEL"): "#795548",
    ("OTHER", "LEVEL"): "#F44336",
}


@dataclass(frozen=True)
class Event:
    timestamp: datetime | float
    state: tuple[str, str]
    note: str


def parse_time(value: str) -> datetime | float:
    """兼容两种 timestamp 口径：绝对时间返回 datetime，相对秒返回 float（同一试次内口径一致）。"""
    try:
        return datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError:
        return float(value)


def delta_seconds(later, earlier) -> float:
    delta = later - earlier
    return delta.total_seconds() if hasattr(delta, "total_seconds") else float(delta)


def state_name(state: tuple[str, str]) -> str:
    activity, terrain = state
    return f"{ACTIVITY_NAMES.get(activity, activity)}·{TERRAIN_NAMES.get(terrain, terrain)}"


def read_annotations(path: Path) -> tuple[dict[str, list[Event]], set[str]]:
    grouped: dict[str, list[Event]] = defaultdict(list)
    invalid_files: set[str] = set()
    required = {"input_file", "timestamp", "activity_truth", "terrain_truth", "notes"}

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            missing = sorted(required - set(reader.fieldnames or []))
            raise ValueError(f"标注文件缺少字段：{', '.join(missing)}")
        for line_number, row in enumerate(reader, 2):
            input_file = (row.get("input_file") or "").strip()
            try:
                if not input_file:
                    raise ValueError("input_file 为空")
                note = (row["notes"] or "").strip().removeprefix("近似：")
                grouped[input_file].append(
                    Event(
                        parse_time(row["timestamp"]),
                        (row["activity_truth"].strip(), row["terrain_truth"].strip()),
                        note,
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                if input_file:
                    invalid_files.add(input_file)
                print(f"[警告] 标注第 {line_number} 行无效：{exc}", file=sys.stderr)

    for events in grouped.values():
        events.sort(key=lambda event: event.timestamp)
    return dict(grouped), invalid_files


def read_sensor(path: Path) -> tuple[list[datetime | float], list[float], list[float]]:
    samples: dict[datetime | float, list[float]] = {}

    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        time_column = next((name for name in (TIME_COLUMN, ELAPSED_COLUMN) if name in fields), None)
        left_column = next((name for name in (LEFT_COLUMN, LEFT_COLUMN_ALT) if name in fields), None)
        missing = sorted({RIGHT_COLUMN} - fields)
        if time_column is None:
            missing.append(f"{TIME_COLUMN} 或 {ELAPSED_COLUMN}")
        if left_column is None:
            missing.append(f"{LEFT_COLUMN} 或 {LEFT_COLUMN_ALT}")
        if missing:
            raise ValueError(f"缺少字段：{', '.join(missing)}")
        for line_number, row in enumerate(reader, 2):
            try:
                timestamp = parse_time(row[time_column])
                right = float(row[RIGHT_COLUMN])
                left = float(row[left_column])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"第 {line_number} 行无效：{exc}") from exc
            totals = samples.setdefault(timestamp, [0.0, 0.0, 0.0])
            totals[0] += right
            totals[1] += left
            totals[2] += 1

    if not samples:
        raise ValueError("没有数据行")
    timestamps = sorted(samples)
    right_values = [samples[t][0] / samples[t][2] for t in timestamps]
    left_values = [samples[t][1] / samples[t][2] for t in timestamps]
    return timestamps, right_values, left_values


def build_spans(
    events: list[Event], end: datetime
) -> list[tuple[datetime, datetime, tuple[str, str]]]:
    spans: list[tuple[datetime, datetime, tuple[str, str]]] = []
    start, state = events[0].timestamp, events[0].state
    for event in events[1:]:
        if event.state != state:
            spans.append((start, event.timestamp, state))
            start, state = event.timestamp, event.state
    spans.append((start, end, state))
    return spans


def layout_notes(
    events: list[Event], start: datetime, end: datetime
) -> tuple[list[tuple[Event, str, float, int]], int]:
    duration = max(delta_seconds(end, start), 1.0)
    lane_ends: list[float] = []
    layout: list[tuple[Event, str, float, int]] = []

    for event in events:
        wrapped = "\n".join(textwrap.wrap(event.note or state_name(event.state), width=17))
        longest = max(map(len, wrapped.splitlines()))
        center = delta_seconds(event.timestamp, start)
        half_width = duration * min(0.075, 0.015 + longest * 0.0035)
        text_center = min(max(center, half_width), duration - half_width)
        left_edge, right_edge = text_center - half_width, text_center + half_width
        lane = next(
            (i for i, lane_end in enumerate(lane_ends) if left_edge > lane_end),
            len(lane_ends),
        )
        if lane == len(lane_ends):
            lane_ends.append(right_edge)
        else:
            lane_ends[lane] = right_edge
        layout.append((event, wrapped, text_center, lane))
    return layout, max(len(lane_ends), 1)


def plot_file(path: Path, events: list[Event], output_dir: Path) -> Path:
    timestamps, right, left = read_sensor(path)
    start, end = timestamps[0], timestamps[-1]
    if isinstance(start, datetime) != isinstance(events[0].timestamp, datetime):
        raise ValueError("标注与传感器的时间口径不一致（绝对时间 vs 相对秒）")
    if any(event.timestamp < start or event.timestamp > end for event in events):
        raise ValueError("标注时间超出传感器数据范围")
    if any(
        a.timestamp == b.timestamp and a.state != b.state
        for a, b in zip(events, events[1:])
    ):
        raise ValueError("同一标注时间存在冲突状态")

    note_layout, lane_count = layout_notes(events, start, end)
    note_height = max(2.0, lane_count * 0.62)
    fig = plt.figure(figsize=(18, 6.5 + note_height), constrained_layout=False)
    grid = fig.add_gridspec(2, 1, height_ratios=[note_height, 5.5], hspace=0.02)
    note_ax = fig.add_subplot(grid[0])
    ax = fig.add_subplot(grid[1], sharex=note_ax)

    present_states: list[tuple[str, str]] = []
    for span_start, span_end, state in build_spans(events, end):
        ax.axvspan(
            span_start,
            span_end,
            color=STATE_COLORS.get(state, "#607D8B"),
            alpha=0.18,
            zorder=0,
        )
        if state not in present_states:
            present_states.append(state)

    ax.plot(timestamps, left, color="#D84315", linewidth=0.85, zorder=3)
    ax.plot(timestamps, right, color="#1565C0", linewidth=0.85, zorder=3)
    for event in events:
        ax.axvline(
            event.timestamp,
            color="#555555",
            linewidth=0.45,
            alpha=0.42,
            zorder=2,
        )

    for event, wrapped, text_seconds, lane in note_layout:
        text_time = start + (end - start) * (
            text_seconds / max(delta_seconds(end, start), 1.0)
        )
        note_ax.annotate(
            wrapped,
            xy=(event.timestamp, 0.02),
            xytext=(text_time, lane_count - lane - 0.5),
            ha="center",
            va="center",
            fontsize=7.5,
            linespacing=1.15,
            bbox={
                "boxstyle": "round,pad=0.25",
                "facecolor": "white",
                "edgecolor": "#777777",
                "linewidth": 0.55,
            },
            arrowprops={
                "arrowstyle": "-",
                "color": "#555555",
                "linewidth": 0.65,
            },
        )

    note_ax.set_ylim(0, lane_count)
    note_ax.set_ylabel("标注备注")
    note_ax.tick_params(axis="x", labelbottom=False, bottom=False)
    note_ax.tick_params(axis="y", left=False, labelleft=False)
    for side in ("top", "right", "left"):
        note_ax.spines[side].set_visible(False)

    if isinstance(start, datetime):
        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        ax.set_xlabel(f"绝对时间（{start:%Y-%m-%d}）")
    else:
        ax.set_xlabel("试次内相对时间 t_seconds（秒）")
    ax.set_xlim(start, end)
    ax.set_ylabel("腿部位置")
    ax.grid(axis="y", color="#BDBDBD", linewidth=0.55, alpha=0.6)
    ax.set_title(path.name, fontsize=14, pad=10)

    handles: list[Line2D | Patch] = [
        Line2D([0], [0], color="#D84315", linewidth=1.5, label="左腿位置"),
        Line2D([0], [0], color="#1565C0", linewidth=1.5, label="右腿位置"),
    ]
    handles.extend(
        Patch(
            facecolor=STATE_COLORS.get(state, "#607D8B"),
            alpha=0.35,
            label=state_name(state),
        )
        for state in present_states
    )
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=min(5, len(handles)),
        frameon=False,
        fontsize=9,
    )
    fig.subplots_adjust(left=0.065, right=0.985, top=0.97, bottom=0.12)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{path.stem}.png"
    fig.savefig(output_path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="原始试次 CSV 目录",
    )
    parser.add_argument(
        "--annotations",
        type=Path,
        nargs="+",
        help="标注 CSV（可多个，如各受试者的 *_state_changes.auto.csv）；默认 INPUT_DIR/state_changes.csv",
    )
    args = parser.parse_args()
    input_dir = args.input_dir.resolve()
    annotation_paths = args.annotations or [input_dir / "state_changes.csv"]
    output_dir = input_dir / "plots"

    if not input_dir.is_dir():
        print(f"[错误] 输入目录不存在：{input_dir}", file=sys.stderr)
        return 1
    missing = [path for path in annotation_paths if not path.is_file()]
    if missing:
        print(f"[错误] 标注文件不存在：{', '.join(map(str, missing))}", file=sys.stderr)
        return 1

    annotations: dict[str, list[Event]] = {}
    invalid_annotation_files: set[str] = set()
    for annotation_path in annotation_paths:
        try:
            grouped, invalid = read_annotations(annotation_path)
        except (OSError, ValueError) as exc:
            print(f"[错误] 无法读取标注文件 {annotation_path}：{exc}", file=sys.stderr)
            return 1
        for name, events in grouped.items():
            annotations.setdefault(name, []).extend(events)
        invalid_annotation_files |= invalid
    for events in annotations.values():
        events.sort(key=lambda event: event.timestamp)

    generated = 0
    annotation_names = {path.name for path in annotation_paths}
    for path in sorted(input_dir.glob("*.csv")):
        if path.name in annotation_names:
            continue
        if path.name in invalid_annotation_files:
            print(f"[跳过] {path.name}：对应标注含无效记录", file=sys.stderr)
            continue
        events = annotations.get(path.name)
        if not events:
            print(f"[跳过] {path.name}：没有对应标注", file=sys.stderr)
            continue
        try:
            output = plot_file(path, events, output_dir)
        except (OSError, ValueError) as exc:
            print(f"[跳过] {path.name}：{exc}", file=sys.stderr)
            continue
        generated += 1
        print(f"[完成] {output}")

    if not generated:
        print("[错误] 没有生成任何图片", file=sys.stderr)
        return 1
    print(f"共生成 {generated} 张图片：{output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
