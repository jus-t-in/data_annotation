#!/usr/bin/env python3
"""人工标定/复核辅助：多通道信号条带图、同口径工作表与参考构建。

四个子命令：
  plots      为目录中每个试次 CSV 生成 30 秒/条带的信号图（不含任何算法边界）
  worksheet  以某受试者参考的行为骨架，生成 t_seconds 留空的工作表（从零标定用）
  revise     把自动标注输出转成 t_seconds 预填的复核工作表（QA 复核修订用）
  build      把填好 t_seconds 的工作表转换为参考格式 CSV（时间吸附到原始采样点）
"""

from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MultipleLocator
except ImportError:
    raise SystemExit("缺少 matplotlib，请先安装项目运行依赖：python3 -m pip install .")

plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "Droid Sans Fallback", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False

TIME_FORMAT = "%Y/%m/%d %H:%M:%S::%f"
TIME_COLUMN = "RX Date/Time"
ELAPSED_COLUMN = "Elapsed (s)"    # 7.27 起的导出格式：试次内相对秒
RIGHT_COLUMN = "左右腿位置/右腿位置"
LEFT_COLUMN = "左右腿位置/左右位置"
LEFT_COLUMN_ALT = "左右腿位置/左腿位置"  # 7.27 起的列名；语义同 LEFT_COLUMN
PITCH_COLUMN = "俯仰与侧倾/俯仰"
GYRO_COLUMNS = ("Gyroscope/Gyro X", "Gyroscope/Gyro Y", "Gyroscope/Gyro Z")
REFERENCE_FIELDS = (
    "session_id",
    "trial_id",
    "input_file",
    "timestamp",
    "activity_truth",
    "terrain_truth",
    "notes",
)
WORKSHEET_FIELDS = ("input_file", "t_seconds", "activity_truth", "terrain_truth", "notes")
FILE_RE = re.compile(r"^(P\d+_S\d+)_(T\d+)(?:_|\.).*\.csv$", re.I)
STRIP_SECONDS = 30.0
STRIPS_PER_PAGE = 6


def parse_timestamp(value: str) -> datetime | float:
    """兼容两种 timestamp 口径：绝对时间返回 datetime，相对秒返回 float（同一试次内口径一致）。"""
    try:
        return datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError:
        return float(value)


def load_trial(path: Path) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or ())
        time_column = next((name for name in (TIME_COLUMN, ELAPSED_COLUMN) if name in fields), None)
        left_column = next((name for name in (LEFT_COLUMN, LEFT_COLUMN_ALT) if name in fields), None)
        if time_column is None or left_column is None:
            raise ValueError(f"{path.name} 缺少时间列或左腿列")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path.name} 没有数据行")
    if time_column == TIME_COLUMN:
        parsed = [datetime.strptime(row[time_column].strip(), TIME_FORMAT) for row in rows]
        start = min(parsed)
        raw = np.array([(value - start).total_seconds() for value in parsed])
    else:
        elapsed = np.array([float(row[time_column]) for row in rows])
        start = None  # 相对秒口径没有钟表时间
        raw = elapsed - elapsed.min()
    seconds, inverse = np.unique(raw, return_inverse=True)
    stamps = []
    for value in seconds:
        index = int(np.flatnonzero(raw == value)[0])
        stamps.append(rows[index][time_column].strip())

    def channel(name: str) -> np.ndarray:
        source = np.array([float(row[name]) for row in rows])
        sums = np.bincount(inverse, weights=source)
        counts = np.bincount(inverse)
        return sums / counts

    gyro = np.column_stack([channel(name) for name in GYRO_COLUMNS])
    slow = uniform_filter1d(gyro, 101, axis=0, mode="nearest")
    return {
        "seconds": seconds,
        "stamps": stamps,
        "start": start,
        "right": savgol_filter(channel(RIGHT_COLUMN), 11, 3),
        "left": savgol_filter(channel(left_column), 11, 3),
        "pitch": uniform_filter1d(channel(PITCH_COLUMN), 25, mode="nearest"),
        "motion": np.linalg.norm(gyro - slow, axis=1),
    }


def plot_trial(path: Path, output_dir: Path) -> list[Path]:
    trial = load_trial(path)
    seconds = trial["seconds"]
    total = float(seconds[-1])
    strips = max(1, math.ceil(total / STRIP_SECONDS))
    pages = max(1, math.ceil(strips / STRIPS_PER_PAGE))
    outputs = []
    for page in range(pages):
        first = page * STRIPS_PER_PAGE
        count = min(STRIPS_PER_PAGE, strips - first)
        fig, axes = plt.subplots(
            count * 2,
            1,
            figsize=(16, 2.6 * count),
            gridspec_kw={"height_ratios": [3, 1] * count, "hspace": 0.55},
            squeeze=False,
        )
        for row in range(count):
            lo = (first + row) * STRIP_SECONDS
            hi = min(lo + STRIP_SECONDS, total)
            mask = (seconds >= lo) & (seconds <= hi)
            leg_ax = axes[row * 2][0]
            motion_ax = axes[row * 2 + 1][0]
            leg_ax.plot(seconds[mask], trial["left"][mask], color="#D84315", lw=0.9, label="左腿位置")
            leg_ax.plot(seconds[mask], trial["right"][mask], color="#1565C0", lw=0.9, label="右腿位置")
            pitch_ax = leg_ax.twinx()
            pitch_ax.plot(seconds[mask], trial["pitch"][mask], color="#616161", lw=0.8, alpha=0.8, label="俯仰(平滑)")
            pitch_ax.set_ylabel("俯仰 °", fontsize=8, color="#616161")
            pitch_ax.tick_params(labelsize=7, colors="#616161")
            motion_ax.plot(seconds[mask], trial["motion"][mask], color="#2E7D32", lw=0.7)
            motion_ax.set_ylabel("运动强度", fontsize=8)
            if trial["start"] is not None:
                clock = trial["start"] + np.timedelta64(int(lo * 1000), "ms")
                clock_note = f"  （条带起点钟表时间 {np.datetime_as_string(np.datetime64(clock), unit='s').split('T')[1]}）"
            else:
                clock_note = ""
            leg_ax.set_title(
                f"{path.name}  t={lo:.0f}–{hi:.0f}s{clock_note}",
                fontsize=9,
                loc="left",
            )
            for axis in (leg_ax, motion_ax):
                axis.set_xlim(lo, min(lo + STRIP_SECONDS, total) if hi > lo else lo + STRIP_SECONDS)
                axis.xaxis.set_major_locator(MultipleLocator(5.0))
                axis.xaxis.set_minor_locator(MultipleLocator(0.5))
                axis.grid(which="major", axis="x", color="#9E9E9E", lw=0.6, alpha=0.7)
                axis.grid(which="minor", axis="x", color="#CFCFCF", lw=0.35, alpha=0.55)
                axis.tick_params(labelsize=7)
            leg_ax.set_ylabel("腿部位置", fontsize=8)
            if row == 0:
                lines1, labels1 = leg_ax.get_legend_handles_labels()
                lines2, labels2 = pitch_ax.get_legend_handles_labels()
                leg_ax.legend(lines1 + lines2, labels1 + labels2, fontsize=7, loc="upper right", ncol=3)
        axes[-1][0].set_xlabel("试次内相对时间 t_seconds（秒）", fontsize=9)
        fig.suptitle(f"{path.name} 标定辅助 第{page + 1}/{pages}页（仅原始信号，无算法边界）", fontsize=11)
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"{path.stem}_aid_p{page + 1}.png"
        fig.savefig(output, dpi=150, bbox_inches="tight")
        plt.close(fig)
        outputs.append(output)
    return outputs


def command_plots(args: argparse.Namespace) -> int:
    generated = []
    for path in sorted(args.input_dir.glob("*.csv")):
        if not FILE_RE.match(path.name):
            continue
        generated.extend(plot_trial(path, args.input_dir / "annotation_aid"))
        print(f"[完成] {path.name}")
    if not generated:
        print("[错误] 没有匹配的试次 CSV", file=sys.stderr)
        return 1
    print(f"共生成 {len(generated)} 张图：{args.input_dir / 'annotation_aid'}")
    return 0


def command_worksheet(args: argparse.Namespace) -> int:
    with args.reference.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["session_id"].upper().startswith(args.skeleton_subject.upper())]
    if not rows:
        print(f"[错误] 参考中没有 {args.skeleton_subject} 的行", file=sys.stderr)
        return 1
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_FIELDS)
        writer.writeheader()
        for row in rows:
            match = FILE_RE.match(row["input_file"])
            trial = match.group(2).upper() if match else row["trial_id"]
            writer.writerow(
                {
                    "input_file": f"{args.subject}_S01_{trial}_.csv",
                    "t_seconds": "",
                    "activity_truth": row["activity_truth"],
                    "terrain_truth": row["terrain_truth"],
                    "notes": row["notes"].removeprefix("近似："),
                }
            )
    print(
        f"已生成工作表：{args.output}\n"
        f"骨架来自 {args.skeleton_subject} 的事件序列，仅作口径提示；"
        f"请按 {args.subject} 的实际信号增删行并填写 t_seconds（试次内相对秒，可到小数）。"
    )
    return 0


def command_revise(args: argparse.Namespace) -> int:
    """把自动标注输出转成 t_seconds 预填的复核工作表：只需修正 QA 项，再经 build 转回参考格式。"""
    rows_out = []
    starts: dict[str, datetime | None] = {}
    for auto_path in args.auto:
        with auto_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            print(f"[错误] {auto_path} 没有数据行", file=sys.stderr)
            return 1
        for row in rows:
            value = parse_timestamp(row["timestamp"])
            if isinstance(value, float):
                seconds = value
            else:
                name = row["input_file"]
                if name not in starts:
                    starts[name] = load_trial(args.data_dir / name)["start"]
                seconds = (value - starts[name]).total_seconds()
            rows_out.append(
                {
                    "input_file": row["input_file"],
                    "t_seconds": f"{seconds:.2f}",
                    "activity_truth": row["activity_truth"],
                    "terrain_truth": row["terrain_truth"],
                    "notes": row["notes"].removeprefix("近似："),
                }
            )
    rows_out.sort(key=lambda item: (item["input_file"], float(item["t_seconds"])))
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=WORKSHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows_out)
    print(
        f"已生成复核工作表：{args.output}（{len(rows_out)} 行，t_seconds 已按自动标注预填）\n"
        f"复核时只改需要修订的行（调 t_seconds、改标签、增删行），并在 notes 里注明人工修订内容；"
        f"完成后用 build 转回参考格式。"
    )
    return 0


def command_build(args: argparse.Namespace) -> int:
    with args.worksheet.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if (row.get("t_seconds") or "").strip()]
    if not rows:
        print("[错误] 工作表中没有已填 t_seconds 的行", file=sys.stderr)
        return 1
    trials = {}
    events = []
    warnings = 0
    for index, row in enumerate(rows, 2):
        name = row["input_file"].strip()
        match = FILE_RE.match(name)
        if not match:
            print(f"[错误] 第 {index} 行 input_file 无法识别：{name}", file=sys.stderr)
            return 1
        if name not in trials:
            trials[name] = load_trial(args.data_dir / name)
        trial = trials[name]
        t_value = float(row["t_seconds"])
        if t_value < 0 or t_value > trial["seconds"][-1] + 0.5:
            print(f"[警告] 第 {index} 行 t_seconds={t_value} 超出 {name} 时长 {trial['seconds'][-1]:.1f}s", file=sys.stderr)
            warnings += 1
        nearest = int(np.argmin(np.abs(trial["seconds"] - t_value)))
        note = row["notes"].strip()
        events.append(
            {
                "session_id": match.group(1).upper(),
                "trial_id": match.group(2).upper(),
                "input_file": name,
                "timestamp": trial["stamps"][nearest],
                "activity_truth": row["activity_truth"].strip(),
                "terrain_truth": row["terrain_truth"].strip(),
                "notes": note if note.startswith("近似：") else f"近似：{note}",
            }
        )
    events.sort(key=lambda item: (item["input_file"], parse_timestamp(item["timestamp"])))
    previous = {}
    for item in events:
        key = item["input_file"]
        state = (item["activity_truth"], item["terrain_truth"])
        if key in previous and state == previous[key]:
            still_level = state == ("STILL", "LEVEL")
            on_stairs = item["terrain_truth"] != "LEVEL"
            if not (on_stairs or still_level):
                print(f"[警告] {key} 在 {item['timestamp']} 出现连续相同状态 {state}（既非楼梯确认也非收尾确认）", file=sys.stderr)
                warnings += 1
        previous[key] = state
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REFERENCE_FIELDS)
        writer.writeheader()
        writer.writerows(events)
    print(f"已写入 {len(events)} 条参考标注：{args.output}（警告 {warnings} 条）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plots = subparsers.add_parser("plots", help="生成标定辅助信号图")
    plots.add_argument("input_dir", type=Path)
    plots.set_defaults(func=command_plots)

    worksheet = subparsers.add_parser("worksheet", help="生成 t_seconds 留空的同口径工作表")
    worksheet.add_argument("--reference", type=Path, required=True)
    worksheet.add_argument("--skeleton-subject", default="P04")
    worksheet.add_argument("--subject", required=True)
    worksheet.add_argument("--output", type=Path, default=Path("reference_worksheet.csv"))
    worksheet.set_defaults(func=command_worksheet)

    revise = subparsers.add_parser("revise", help="把自动标注输出转成 t_seconds 预填的复核工作表")
    revise.add_argument("auto", type=Path, nargs="+", help="自动标注 CSV（可多个，如各受试者的 *_state_changes.auto.csv）")
    revise.add_argument("--data-dir", type=Path, required=True, help="原始试次 CSV 目录（绝对时间口径换算相对秒时需要）")
    revise.add_argument("--output", type=Path, default=Path("review_worksheet.csv"))
    revise.set_defaults(func=command_revise)

    build = subparsers.add_parser("build", help="把填好的工作表转换为参考格式 CSV")
    build.add_argument("worksheet", type=Path)
    build.add_argument("--data-dir", type=Path, required=True)
    build.add_argument("--output", type=Path, default=Path("state_changes.csv"))
    build.set_defaults(func=command_build)

    args = parser.parse_args()
    return args.func(args)


def cli() -> int:
    try:
        return main()
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
