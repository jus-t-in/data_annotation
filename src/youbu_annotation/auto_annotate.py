#!/usr/bin/env python3
"""Offline annotation for protocol-compatible T01-T04 gait trials.

边界一律锚定在信号证据上：脚步 onset、自校准运动阈值沿、俯仰带迁移。
试次协议只用于地形候选顺序筛选与 QA 合理性检查，不参与活动标签分配，
也不按协议虚构缺失事件。检测常量的取值依据见 docs/adr/0003。
"""

from __future__ import annotations

import argparse
import csv
from itertools import combinations
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy.ndimage import binary_closing, uniform_filter1d
from scipy.signal import find_peaks, peak_widths, savgol_filter


TIME_FORMAT = "%Y/%m/%d %H:%M:%S::%f"
TIME_COLUMN = "RX Date/Time"
ELAPSED_COLUMN = "Elapsed (s)"    # 7.27 起的导出格式：试次内相对秒
RIGHT_COLUMN = "左右腿位置/右腿位置"
LEFT_COLUMN = "左右腿位置/左右位置"
LEFT_COLUMN_ALT = "左右腿位置/左腿位置"  # 7.27 起的列名；语义同 LEFT_COLUMN
PITCH_COLUMN = "俯仰与侧倾/俯仰"
GYRO_COLUMNS = (
    "Gyroscope/Gyro X",
    "Gyroscope/Gyro Y",
    "Gyroscope/Gyro Z",
)
ACCEL_COLUMNS = (
    "Accelerometer/Accelerometer X",
    "Accelerometer/Accelerometer Y",
    "Accelerometer/Accelerometer Z",
)
OUTPUT_FIELDS = (
    "session_id",
    "trial_id",
    "input_file",
    "timestamp",
    "activity_truth",
    "terrain_truth",
    "notes",
)
FILE_RE = re.compile(r"^(P\d+_S\d+)_(T0[1-4])(?:_|\.).*\.csv$", re.I)

# ---- 检测常量。数值依据 7.21 开发集特征研究，推导过程见 docs/adr/0003 ----
DATA_GAP_S = 1.0                # 原始采样间隔超过此值视为数据断档（QA 标记）
CAL_START_S, CAL_END_S = 0.3, 2.5   # 自校准窗口：协议保证文件开头静止 3–5 秒
NOISE_K_MAD = 8.0               # 阈值 = 静止中位数 + K × MAD
MOTION_CLAMP = (7.1, 12.0)      # 下限即历史全局阈值（介于"动作间整理"3–7 与动作 12+ 之间）；
MOTION_FALLBACK = 7.1           # 自校准只向上适配高噪声设备，安静设备保持 7.1
LOCAL_CLAMP = (5.0, 9.0)
LOCAL_FALLBACK = 5.0
BOUT_MERGE_GAP_S = 0.4          # 运动段合并：需小于转身对间隔（实测 0.47s）
BOUT_MIN_S = 0.4
WALK_JOIN_GAP_S = 0.9           # 相邻 WALKING 段（转身缓冲）合并上限；平台停顿 ≥1s 必须保留
STEP_ONSET_BIAS_S = -0.14       # 参考边界在检测 onset 之前 ~0.14s（peak_widths 半高左沿滞后于视觉摆动起点）
ENTRY_CROSS_LEAD_S = 0.1        # 参考入口中位领先持续穿越 0.11s：选步锚 = 穿越 − 该值
EXIT_ONSET_MAX_GAP_S = 0.5      # 出口最近 onset 距穿越超过此值时（到平台即停无后续步），以穿越为准
MIN_PLATFORM_S = 1.0            # 相邻楼梯段最小平台间隙（参考中最短平台 1.07s）
CADENCE_EXIT_RATIO = 0.72       # 出口步频修剪：其后连续步间隔 < 0.72×楼梯核心间隔视为已进入平地快走（下楼 0.87s vs 快走 0.59s）
WEAK_PITCH_DELTA = 3.0          # 楼梯带与平地中心差小于此值（P05 下楼 0.6–2.2°、P04_T02 2.9°）时俯仰锚不可靠，改用冲击锚
IMPACT_ANCHOR_RATIO = 0.6       # 冲击锚：越过 0.6×段核心冲击视为进入/离开楼梯
IMPACT_EXIT_HOLD_S = 2.5        # 冲击出口需持续低落 2.5s（楼梯中途停顿最长 1.76s，不会误触发）
STEP_PROMINENCE = 0.10
STEP_MIN_GAP = 25               # find_peaks distance（0.25 s）
PITCH_SMOOTH_N = 75
PITCH_CROSS_SMOOTH_N = 25       # 穿越检测用轻平滑（75 样本平滑有 ~0.4s 群延迟）
PITCH_MIN_SEPARATION = 1.2      # 三聚类中心最小间隔（度）
STAIR_MERGE_GAP_S = 1.5
STAIR_MIN_S = 1.8
STAIR_JOIN_GAP_S = 3.5
ENTRY_SEARCH_BACK_S, ENTRY_SEARCH_FWD_S = 3.0, 1.5
EXIT_SEARCH_BACK_S, EXIT_SEARCH_FWD_S = 2.5, 3.0
IMPACT_BASE_N = 101             # 落脚冲击：加速度模相对 1s 慢基线的偏差，0.5s 平均
IMPACT_AVG_N = 51
IMPACT_LOW_RATIO = 0.55         # 平地冲击 ≈ 0.35–0.55 × 楼梯段核心值（速度自适应的相对判据）
IMPACT_TRIM_MIN_S = 1.2         # 段首/尾持续低冲击超过此时长视为俯仰带渗漏，予以修剪
CONFIRM_MIN_S, CONFIRM_MAX_S = 0.2, 1.5
PAUSE_LEG_SPEED = 0.35          # 楼梯中途停顿：腿部速度低于此值
PAUSE_MIN_S = 0.22              # 参考停顿最短 0.25 s
PAUSE_MERGE_GAP_S = 0.5         # 停顿内短暂抖动不拆分（并脚静止常有重心微调）
PAUSE_DEPTH_SPEED = 0.30        # 停顿深度：段内腿速 25 分位（参考停顿 ≤0.175；合并桥接出的假停顿 0.455）
PAUSE_GUARD_S = 0.8             # 距楼梯进入/退出边界的保护带
BEND_PITCH_PTP = 30.0           # 弯腰俯仰摆幅 38.6–41.9 vs 深蹲 16.3–21.1
ACTION_SPAN = 0.7               # 大幅腿部动作的单腿摆幅门限
ACTION_MIN_S = 1.5
SQUAT_LEG_CORR_STRONG = -0.7    # 深蹲组（多次重复）：通道相关 −0.82..−0.93
SQUAT_LEG_CORR_WEAK = -0.4      # 单次深蹲相关较弱（−0.49），但交替次数 il≤2 可与步态碎片（il≥3）硬区分
SQUAT_MAX_INTERLEAVE = 2
SQUAT_MAX_PEAK_RATE = 1.05      # 深蹲组（含恢复波动）峰频 ≈1.0/s，快步态 ≥1.2/s
HKA_MIN_SPAN = 1.55             # 交替高抬腿双腿最小摆幅 1.66–1.89 vs 行走 ≤1.50；与 prom 门限联合区分
HKA_MIN_PROM = 1.40             # 峰显著度中位 1.52–1.69（P06 1.45）vs 行走 ≤1.25
HKS_DOMINANCE = 0.75            # 单腿高抬：峰集中于一条腿的比例（7.21 均为 1.00；行走 ≤0.57）
TURN_GYRO_Y = 25.0              # 转身 1s 窗 |gyroY 均值| 峰值；转身段整体均值 35–54 vs 其他 ≤6.5
TURN_WINDOW_N = 100             # 转身检测滑窗（1.0 s）
TURN_PAIR_MAX_S = 6.5           # 双极 gyroY 峰的粘连转身对在零穿越处拆分
WALK_MIN_SPAN = 0.55            # 行走单腿摆幅下限（拖步 ≤0.47）
LEG_QUIET_SPEED = 0.22          # 腿部静止切分：陀螺不安静但腿停下的"整理期"
LEG_QUIET_MIN_S = 0.7
STITCH_GAPS = {"HIGH_KNEE_SINGLE": 0.8, "WALKING": 0.9}   # 同标签缝合间隔；TURN 不缝合
STITCH_DEFAULT_GAP_S = 1.35     # 拖步组内低谷可达 1.24s；不同动作最小间隔 1.5s
OTHER_ABSORB_GAP_S = 0.8        # OTHER 碎片与邻段合并重分类（弯腰谷底停顿会劈开动作）
TURN_MAX_S = 3.0
WALK_MIN_INTERLEAVE = 3         # 行走：左右交替 onset 次数
SHUFFLE_MAX_SPAN = 0.55         # 拖步单腿摆幅 ≤0.46；≥5.5s 的真实行走摆幅 ≥0.94
SHUFFLE_MIN_S = 6.0
OTHER_MIN_S = 0.5               # 低于此时长视为不确定小幅波动，不标注
WALK_SLIVER_S = 0.75            # 仅"平地行走"碎片坍缩；参考中最短真实到平地短走 0.85s
TAIL_CONFIRM_GAP_S = 2.5        # 收尾确认：最后切换距文件尾至少此间隔
TAIL_CONFIRM_OFFSET_S = 0.2
# ---- 垂直加速度裁决器（ADR-0005）：俯仰弱分离时的地形边界仲裁 ----
GRAVITY_MS2 = 9.80665
VERT_GRAVITY_N = 801            # 重力方向低通窗（8.0s）：匀速上下楼的垂直加速度均值为零，长窗不会吃掉楼梯信号
VERT_WINDOW_N = 301             # 特征滑窗（3.0s）：覆盖 2–3 个步态周期
VERT_MIN_LEVEL_S = 3.0          # 自归一化基准所需的最短平地行走样本，不足则不启用裁决
VERT_RMS_STAIR = 1.45           # 自归一化垂直 RMS 门限；7.21 下楼 2.08–2.24×平地、上楼 1.71–1.74×、平地 ≈1.0×
VERT_MARGIN_S = 1.0             # 裁决边界与俯仰边界差小于此值时不改写（避免在俯仰已可靠处引入抖动）
VERT_MAX_SHIFT_S = 12.0         # 单侧最大改写幅度；超出视为证据异常，保留俯仰边界并报 QA
VERT_SCAN_MIN_S = 4.0           # 骨架缺段重扫：候选段最短时长
VERT_CONTRAST_MIN = 1.6         # 宿主行走段内 p90/p20 低于此值视为无可用对比，不裁决
VERT_CONTRAST_RATIO = 0.45      # 鼓包门限 = p20 + 该比例 ×(p90 − p20)
VERT_REFIT_MIN_S = 3.0          # 边界改写门限：与鼓包边界差小于此值不动（俯仰在多数段上已够准）
VERT_MIN_OVERLAP_S = 1.0        # 楼梯段与鼓包的最小重叠，低于此值视为"无垂直支撑"
VERT_HOST_LEVEL_S = 4.0         # 宿主行走段内非楼梯部分的最短时长：不足则段内无平地基线，鼓包不可信
VERT_ASYM_SPLIT = 1.55          # 上楼/下楼的垂直正负峰值比分界（速度无关）：逐宿主中位在
                                # 7.21 T02 上楼 1.89/2.01 vs 下楼 1.45/1.47、7.27 P06_T02
                                # 上楼 1.65–2.14 vs 下楼 0.99–1.38，两侧均不重叠
VERT_ABSORB_GAP_S = 4.5         # 吸收相邻楼梯宿主的最大间隔（楼梯中途停顿 + 并脚静止实测 ≤4.1s）
# 阈值按纯证据规则在 7.21 上的诚实水平设定（ADR-0003）：序列 100% 一致是硬性前提，
# 时间误差集中于俯仰弱分离的下楼边界（P05 下楼 Δ<2.5°）
VALIDATION_THRESHOLDS = {"median": 0.25, "p95": 2.5, "max": 5.0}


def parse_timestamp(value: str) -> datetime | float:
    """兼容两种 timestamp 口径：绝对时间（RX Date/Time）返回 datetime，相对秒（Elapsed (s)）返回 float。

    同一试次内口径一致，仅用于排序与同试次求差；两种类型不可互相比较。
    """
    try:
        return datetime.strptime(value.strip(), TIME_FORMAT)
    except ValueError:
        return float(value)


def timestamp_delta_seconds(left: str, right: str) -> float:
    delta = parse_timestamp(left) - parse_timestamp(right)
    return abs(delta.total_seconds() if isinstance(delta, timedelta) else delta)


@dataclass(frozen=True)
class Event:
    session_id: str
    trial_id: str
    input_file: str
    timestamp: str
    activity_truth: str
    terrain_truth: str
    notes: str


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    label: str


@dataclass(frozen=True)
class Step:
    onset: float
    peak: float
    leg: str
    prominence: float


def vertical_features(accel: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """垂直（重力方向）线性加速度及其滑窗特征，用于地形裁决（ADR-0005）。

    重力方向取长窗低通：匀速上下楼时垂直加速度的均值为零，长窗只吸收重力而不吸收楼梯信号。
    a_up 为比力沿该方向的分量减去重力模，朝上为正，单位 m/s²；与设备安装角无关。
    返回 (a_up, 正负峰值比, RMS)：峰值比刻画上楼蹬地推力的不对称，RMS 刻画落脚冲击幅度。
    """
    gravity = uniform_filter1d(accel, VERT_GRAVITY_N, axis=0, mode="nearest")
    norm = np.linalg.norm(gravity, axis=1)
    a_up = (np.einsum("ij,ij->i", accel, gravity / np.maximum(norm, 1e-9)[:, None]) - norm) * GRAVITY_MS2
    half = VERT_WINDOW_N // 2
    windows = sliding_window_view(np.pad(a_up, half, mode="edge"), VERT_WINDOW_N)
    high = np.empty(len(windows), dtype=float)
    low = np.empty(len(windows), dtype=float)
    # np.percentile copies its input; chunking keeps the same windows and values
    # without materializing the full N x VERT_WINDOW_N temporary array.
    for start in range(0, len(windows), 20_000):
        stop = min(start + 20_000, len(windows))
        high[start:stop], low[start:stop] = np.percentile(windows[start:stop], (95, 5), axis=-1)
    asym = high / np.maximum(np.abs(low), 1e-9)
    rms = np.sqrt(uniform_filter1d(a_up**2, VERT_WINDOW_N, mode="nearest"))
    return a_up, asym, rms


class Signal:
    dt = 0.01

    def __init__(self, path: Path):
        self.path = path
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or ())
            time_column = next((name for name in (TIME_COLUMN, ELAPSED_COLUMN) if name in fields), None)
            left_column = next((name for name in (LEFT_COLUMN, LEFT_COLUMN_ALT) if name in fields), None)
            missing = sorted({RIGHT_COLUMN, PITCH_COLUMN, *GYRO_COLUMNS, *ACCEL_COLUMNS} - fields)
            if time_column is None:
                missing.append(f"{TIME_COLUMN} 或 {ELAPSED_COLUMN}")
            if left_column is None:
                missing.append(f"{LEFT_COLUMN} 或 {LEFT_COLUMN_ALT}")
            if missing:
                raise ValueError(f"{path.name} 缺少字段：{', '.join(missing)}")
            rows = list(reader)
        if not rows:
            raise ValueError(f"{path.name} 没有数据行")

        if time_column == TIME_COLUMN:
            parsed = [datetime.strptime(row[time_column].strip(), TIME_FORMAT) for row in rows]
            start = min(parsed)
            raw_seconds = np.array([(value - start).total_seconds() for value in parsed])
        else:
            elapsed = np.array([float(row[time_column]) for row in rows])
            raw_seconds = elapsed - elapsed.min()
        self.raw_seconds, first_indices, inverse = np.unique(
            raw_seconds,
            return_index=True,
            return_inverse=True,
        )
        self.raw_timestamps = [rows[int(index)][time_column].strip() for index in first_indices]

        def channel(name: str) -> np.ndarray:
            source = np.array([float(row[name]) for row in rows])
            sums = np.bincount(inverse, weights=source)
            counts = np.bincount(inverse)
            return np.interp(self.grid, self.raw_seconds, sums / counts)

        self._prepare(channel, left_column)

    @classmethod
    def from_normalized(
        cls,
        path: Path,
        raw_seconds: np.ndarray,
        raw_timestamps: list[str],
        channels: dict[str, np.ndarray],
        left_column: str,
    ) -> "Signal":
        signal = cls.__new__(cls)
        signal.path = path
        signal.raw_seconds = raw_seconds
        signal.raw_timestamps = raw_timestamps

        def channel(name: str) -> np.ndarray:
            return np.interp(signal.grid, signal.raw_seconds, channels[name])

        signal._prepare(channel, left_column)
        return signal

    def _prepare(self, channel, left_column: str) -> None:
        self.grid = np.arange(0.0, self.raw_seconds[-1] + self.dt / 2, self.dt)
        diffs = np.diff(self.raw_seconds)
        self.gaps = [
            (float(self.raw_seconds[i]), float(self.raw_seconds[i + 1]))
            for i in np.flatnonzero(diffs > DATA_GAP_S)
        ]
        self.right = savgol_filter(channel(RIGHT_COLUMN), 11, 3)
        self.left = savgol_filter(channel(left_column), 11, 3)
        self.pitch = channel(PITCH_COLUMN)
        self.gyro = np.column_stack([channel(name) for name in GYRO_COLUMNS])
        self.accel = np.column_stack([channel(name) for name in ACCEL_COLUMNS])

        gyro_slow = uniform_filter1d(self.gyro, 101, axis=0, mode="nearest")
        self.motion = np.linalg.norm(self.gyro - gyro_slow, axis=1)
        gyro_mean = uniform_filter1d(self.gyro, 21, axis=0, mode="nearest")
        gyro_square_mean = uniform_filter1d(self.gyro**2, 21, axis=0, mode="nearest")
        self.local_motion = np.sqrt(np.maximum(gyro_square_mean - gyro_mean**2, 0.0).sum(axis=1))
        self.leg_speed = uniform_filter1d(
            np.hypot(np.gradient(self.right, self.dt), np.gradient(self.left, self.dt)),
            11,
            mode="nearest",
        )
        accel_mag = np.linalg.norm(self.accel, axis=1)
        self.impact = uniform_filter1d(
            np.abs(accel_mag - uniform_filter1d(accel_mag, IMPACT_BASE_N, mode="nearest")),
            IMPACT_AVG_N,
            mode="nearest",
        )
        self.a_up, self.vert_asym, self.vert_rms = vertical_features(self.accel)
        self.motion_threshold, self.local_threshold, self.calibration = self._calibrate()

    def _calibrate(self) -> tuple[float, float, dict]:
        info: dict[str, dict] = {}

        def derive(values: np.ndarray, clamp: tuple[float, float], fallback: float, key: str) -> float:
            window = (self.grid >= CAL_START_S) & (self.grid <= min(CAL_END_S, self.grid[-1]))
            sample = values[window]
            if len(sample) < 100:
                info[key] = {"source": "fallback", "threshold": fallback, "reason": "静止窗过短"}
                return fallback
            med = float(np.median(sample))
            mad = float(np.median(np.abs(sample - med)))
            raw = med + NOISE_K_MAD * mad
            if raw > clamp[1] or float(np.percentile(sample, 95)) > 3.0 * max(raw, 1e-6):
                info[key] = {"source": "fallback", "threshold": fallback, "reason": "开头静止被运动污染"}
                return fallback
            threshold = float(np.clip(raw, *clamp))
            info[key] = {
                "source": "calibrated",
                "threshold": round(threshold, 3),
                "still_median": round(med, 3),
                "still_mad": round(mad, 3),
            }
            return threshold

        motion = derive(self.motion, MOTION_CLAMP, MOTION_FALLBACK, "motion")
        local = derive(self.local_motion, LOCAL_CLAMP, LOCAL_FALLBACK, "local_motion")
        return motion, local, info

    def snap(self, seconds: float) -> str:
        index = int(np.argmin(np.abs(self.raw_seconds - seconds)))
        return self.raw_timestamps[index]

    def index(self, seconds: float) -> int:
        return int(np.clip(round(seconds / self.dt), 0, len(self.grid) - 1))


def mask_segments(
    grid: np.ndarray,
    mask: np.ndarray,
    *,
    merge_gap: float = 0.0,
    min_duration: float = 0.0,
    label: str = "",
) -> list[Segment]:
    if merge_gap:
        width = max(1, round(merge_gap / (grid[1] - grid[0])))
        mask = binary_closing(mask, structure=np.ones(width, dtype=bool))
    changes = np.flatnonzero(np.diff(np.r_[False, mask, False].astype(np.int8)))
    result = []
    for start, stop in changes.reshape(-1, 2):
        end = grid[min(stop, len(grid) - 1)]
        if end - grid[start] >= min_duration:
            result.append(Segment(float(grid[start]), float(end), label))
    return result


def motion_bouts(signal: Signal, merge_gap: float, min_duration: float) -> list[Segment]:
    mask = signal.motion >= signal.motion_threshold
    return mask_segments(
        signal.grid,
        mask,
        merge_gap=merge_gap,
        min_duration=min_duration,
        label="ACTIVE",
    )


def refine_leg_start(signal: Signal, coarse: float) -> float:
    lo, hi = max(0.0, coarse - 0.5), min(signal.grid[-1], coarse + 0.7)
    baseline_mask = (signal.grid >= max(0.0, lo - 0.8)) & (signal.grid < lo)
    if baseline_mask.sum() < 5:
        baseline_mask = signal.grid <= min(3.0, signal.grid[-1])
    right0 = float(np.median(signal.right[baseline_mask]))
    left0 = float(np.median(signal.left[baseline_mask]))
    deviation = np.maximum(abs(signal.right - right0), abs(signal.left - left0))
    mask = (signal.grid >= lo) & (signal.grid <= hi)
    candidates = np.flatnonzero(mask & (deviation >= 0.02))
    if not len(candidates):
        return coarse
    return float(signal.grid[candidates[0]])


def refine_motion_edge(signal: Signal, coarse: float, rising: bool) -> float:
    lo, hi = max(0.0, coarse - 0.45), min(signal.grid[-1], coarse + 0.45)
    mask = signal.motion >= signal.motion_threshold
    changes = np.flatnonzero(np.diff(mask.astype(np.int8))) + 1
    direction = 1 if rising else -1
    candidates = [
        index
        for index in changes
        if int(mask[index]) - int(mask[index - 1]) == direction
        and lo <= signal.grid[index] <= hi
    ]
    if not candidates:
        return coarse
    index = min(candidates, key=lambda item: abs(signal.grid[item] - coarse))
    return float(signal.grid[index])


def transition_times(signal: Signal, rising: bool, start: float, end: float) -> list[float]:
    mask = signal.motion >= signal.motion_threshold
    changes = np.flatnonzero(np.diff(mask.astype(np.int8))) + 1
    direction = 1 if rising else -1
    return [
        float(signal.grid[index])
        for index in changes
        if int(mask[index]) - int(mask[index - 1]) == direction
        and start <= signal.grid[index] <= end
    ]


def steps(signal: Signal) -> list[Step]:
    result = []
    for leg, values, sign in (("R", signal.right, 1.0), ("L", signal.left, -1.0)):
        oriented = sign * values
        peaks, properties = find_peaks(oriented, prominence=STEP_PROMINENCE, distance=STEP_MIN_GAP)
        left_ips = peak_widths(oriented, peaks, rel_height=0.5)[2]
        for peak, onset, prominence in zip(peaks, left_ips, properties["prominences"]):
            onset_index = int(round(onset))
            if signal.grid[peak] - signal.grid[onset_index] > 1.5:
                continue
            result.append(Step(float(signal.grid[onset_index]), float(signal.grid[peak]), leg, float(prominence)))
    ordered = sorted(result, key=lambda item: item.onset)
    deduplicated = []
    for item in ordered:
        if deduplicated and item.leg == deduplicated[-1].leg and item.onset - deduplicated[-1].onset < 0.2:
            if item.prominence > deduplicated[-1].prominence:
                deduplicated[-1] = item
            continue
        deduplicated.append(item)
    return deduplicated


# ---- 活动分类：逐段特征判定，不使用协议顺序 ----


@dataclass(frozen=True)
class BoutFeatures:
    duration: float
    right_span: float
    left_span: float
    pitch_ptp: float
    gyro_y_signed_peak: float
    leg_corr: float
    prom_median: float
    interleave: int
    peak_rate: float
    dominance: float


def bout_features(signal: Signal, gait: list[Step], segment: Segment) -> BoutFeatures:
    mask = (signal.grid >= segment.start) & (signal.grid <= segment.end)
    inseg = [step for step in gait if segment.start <= step.onset <= segment.end]
    right_peaks = [step for step in inseg if step.leg == "R"]
    left_peaks = [step for step in inseg if step.leg == "L"]
    interleave = sum(1 for a, b in zip(inseg, inseg[1:]) if a.leg != b.leg)
    duration = max(segment.end - segment.start, 1e-6)
    counts = (len(right_peaks), len(left_peaks))
    gyro_window = uniform_filter1d(signal.gyro[:, 1], TURN_WINDOW_N, mode="nearest")[mask]
    signed_peak = 0.0
    if len(gyro_window):
        signed_peak = float(gyro_window[np.argmax(np.abs(gyro_window))])
    leg_corr = 0.0
    right_vel = np.diff(signal.right[mask]) if mask.sum() >= 21 else np.array([])
    left_vel = np.diff(signal.left[mask]) if mask.sum() >= 21 else np.array([])
    if len(right_vel) and np.std(right_vel) > 1e-6 and np.std(left_vel) > 1e-6:
        leg_corr = float(np.corrcoef(right_vel, left_vel)[0, 1])
    return BoutFeatures(
        duration=duration,
        right_span=float(np.ptp(signal.right[mask])) if mask.any() else 0.0,
        left_span=float(np.ptp(signal.left[mask])) if mask.any() else 0.0,
        pitch_ptp=float(np.ptp(signal.pitch[mask])) if mask.any() else 0.0,
        gyro_y_signed_peak=signed_peak,
        leg_corr=leg_corr,
        prom_median=float(np.median([step.prominence for step in inseg])) if inseg else 0.0,
        interleave=interleave,
        peak_rate=len(inseg) / duration,
        dominance=max(counts) / max(1, len(inseg)),
    )


def classify_bout(f: BoutFeatures) -> str | None:
    small, large = sorted((f.right_span, f.left_span))
    if small >= ACTION_SPAN and f.pitch_ptp >= BEND_PITCH_PTP and f.duration >= ACTION_MIN_S:
        return "BEND"
    if small >= ACTION_SPAN and f.duration >= ACTION_MIN_S and f.peak_rate <= SQUAT_MAX_PEAK_RATE:
        if f.leg_corr <= SQUAT_LEG_CORR_STRONG or (
            f.leg_corr <= SQUAT_LEG_CORR_WEAK and f.interleave <= SQUAT_MAX_INTERLEAVE
        ):
            return "SQUAT"
    if small >= HKA_MIN_SPAN and f.prom_median >= HKA_MIN_PROM and f.leg_corr > SQUAT_LEG_CORR_STRONG:
        return "HIGH_KNEE_ALTERNATING"
    if large < ACTION_SPAN and abs(f.gyro_y_signed_peak) >= TURN_GYRO_Y and f.duration <= TURN_MAX_S:
        return "TURN"
    if large >= ACTION_SPAN and small < ACTION_SPAN and f.dominance >= HKS_DOMINANCE:
        return "HIGH_KNEE_SINGLE"
    if f.interleave >= WALK_MIN_INTERLEAVE and large >= WALK_MIN_SPAN:
        return "WALKING"
    if large <= SHUFFLE_MAX_SPAN and f.duration >= SHUFFLE_MIN_S:
        return "SHUFFLE"
    if f.duration >= OTHER_MIN_S:
        return "OTHER"
    return None


def split_on_quiet_legs(signal: Signal, bouts: list[Segment]) -> list[Segment]:
    """陀螺仪不安静但双腿停摆的"整理期"（拖步→站姿调整之间等）按腿部静止切分。"""
    quiet = signal.leg_speed < LEG_QUIET_SPEED
    result = []
    for item in bouts:
        window = (signal.grid >= item.start) & (signal.grid <= item.end)
        mask = np.zeros(len(signal.grid), dtype=bool)
        mask[window] = quiet[window]
        cuts = [
            pause
            for pause in mask_segments(signal.grid, mask, min_duration=LEG_QUIET_MIN_S)
            if pause.start - item.start >= BOUT_MIN_S and item.end - pause.end >= BOUT_MIN_S
        ]
        cursor = item.start
        for pause in cuts:
            result.append(Segment(cursor, pause.start, item.label))
            cursor = pause.end
        result.append(Segment(cursor, item.end, item.label))
    return result


def split_turn_pairs(signal: Signal, bouts: list[Segment]) -> list[Segment]:
    """左右转身粘成一个 bout 时（间隙陀螺不安静），按 gyroY 双极峰在零穿越处拆开。"""
    windowed = uniform_filter1d(signal.gyro[:, 1], TURN_WINDOW_N, mode="nearest")
    result = []
    for item in bouts:
        mask = (signal.grid >= item.start) & (signal.grid <= item.end)
        duration = item.end - item.start
        values = windowed[mask]
        spans = (
            (float(np.ptp(signal.right[mask])), float(np.ptp(signal.left[mask])))
            if mask.any()
            else (0.0, 0.0)
        )
        if (
            max(spans) >= ACTION_SPAN
            or duration > TURN_PAIR_MAX_S
            or not len(values)
            or values.min() > -TURN_GYRO_Y
            or values.max() < TURN_GYRO_Y
        ):
            result.append(item)
            continue
        lo, hi = sorted((int(np.argmin(values)), int(np.argmax(values))))
        between = values[lo : hi + 1]
        zero_crossings = np.flatnonzero(np.diff(np.sign(between)))
        if not len(zero_crossings):
            result.append(item)
            continue
        cut = item.start + (lo + int(zero_crossings[0])) * signal.dt
        if cut - item.start < 0.4 or item.end - cut < 0.4:
            result.append(item)
            continue
        result.append(Segment(item.start, cut, item.label))
        result.append(Segment(cut, item.end, item.label))
    return result


def resolve_turns(labeled: list[Segment], features: dict[tuple[float, float], BoutFeatures]) -> tuple[list[Segment], list[str]]:
    flags = []
    seen = set()
    result = []
    for item in labeled:
        if item.label != "TURN":
            result.append(item)
            continue
        direction = "TURNING_LEFT" if features[(item.start, item.end)].gyro_y_signed_peak < 0 else "TURNING_RIGHT"
        if direction in seen:
            flags.append(f"检测到重复的{direction}，改标 OTHER")
            result.append(Segment(item.start, item.end, "OTHER"))
        else:
            seen.add(direction)
            result.append(Segment(item.start, item.end, direction))
    return result, flags


def stitch_bouts(signal: Signal, gait: list[Step], labeled: list[Segment]) -> list[Segment]:
    """同标签缝合相邻段并整段重分类：长段特征比碎片可靠（拖步/交替高抬腿易被短暂低谷切碎）。"""
    for _ in range(2):
        merged: list[Segment] = []
        for item in labeled:
            if merged:
                previous = merged[-1]
                gap = item.start - previous.end
                limit = STITCH_GAPS.get(item.label, STITCH_DEFAULT_GAP_S)
                if item.label == previous.label != "TURN" and gap <= limit:
                    merged[-1] = Segment(previous.start, item.end, previous.label)
                    continue
                if (
                    "OTHER" in (previous.label, item.label)
                    and "TURN" not in (previous.label, item.label)
                    and gap <= OTHER_ABSORB_GAP_S
                ):
                    candidate = Segment(previous.start, item.end, "")
                    label = classify_bout(bout_features(signal, gait, candidate))
                    if label in {"BEND", "SQUAT", "HIGH_KNEE_ALTERNATING", "HIGH_KNEE_SINGLE", "SHUFFLE"}:
                        merged[-1] = Segment(previous.start, item.end, label)
                        continue
            merged.append(item)
        relabeled = []
        for item in merged:
            label = classify_bout(bout_features(signal, gait, item)) or item.label
            if item.label == "TURN" and label != "TURN":
                label = item.label  # 拆分出的转身段保持转身
            relabeled.append(Segment(item.start, item.end, label))
        labeled = relabeled
    return labeled


def activity_segments(signal: Signal, gait: list[Step]) -> tuple[list[Segment], list[str]]:
    bouts = []
    for item in motion_bouts(signal, BOUT_MERGE_GAP_S, BOUT_MIN_S):
        if item.end <= CAL_END_S and item.end - item.start < 1.0:
            continue  # 校准静止窗内的微小波动
        bouts.append(item)
    bouts = split_on_quiet_legs(signal, bouts)
    bouts = split_turn_pairs(signal, bouts)
    labeled = []
    for item in bouts:
        label = classify_bout(bout_features(signal, gait, item))
        if label is None:
            continue
        labeled.append(Segment(item.start, item.end, label))

    merged = stitch_bouts(signal, gait, labeled)
    features = {(item.start, item.end): bout_features(signal, gait, item) for item in merged}
    merged, flags = resolve_turns(merged, features)

    refined = []
    for item in merged:
        if item.label == "WALKING":
            start = refine_leg_start(signal, refine_motion_edge(signal, item.start, True))
            falls = transition_times(signal, False, item.end - 0.7, item.end + 0.05)
            end = falls[-1] if falls else refine_motion_edge(signal, item.end, False)
        else:
            start = refine_motion_edge(signal, item.start, True)
            end = refine_motion_edge(signal, item.end, False)
        if end > start:
            refined.append(Segment(start, end, item.label))
    return refined, flags


# ---- 地形：俯仰三聚类 + 协议顺序软筛选 + 脚步 onset 对齐 ----


def ordered_pitch_centers(values: np.ndarray, percentiles: tuple[float, float, float]) -> np.ndarray:
    centers = np.percentile(values, list(percentiles)).astype(float)
    for _ in range(30):
        groups = np.argmin(abs(values[:, None] - centers[None, :]), axis=1)
        updated = np.array(
            [values[groups == index].mean() if np.any(groups == index) else centers[index] for index in range(3)]
        )
        updated.sort()
        if np.allclose(updated, centers, atol=1e-3):
            break
        centers = updated
    return centers


def vertical_bumps(signal: Signal, walking: list[Segment]) -> list[Segment]:
    """行走掩码内垂直 RMS 的局部鼓包，即楼梯步态的候选区间（ADR-0005）。

    门限按每个宿主行走段自适应：垂直 RMS 随步速整体抬升（7.21 中第 3 轮快走平地 2.02
    已超过第 1 轮下楼 1.60），全局阈值必然重叠；而同一宿主段内"楼梯高于其相邻平地"
    的局部对比在 7.21 的 14 组上下楼中无一例外。
    """
    result: list[Segment] = []
    for host in walking:
        span = (signal.grid >= host.start) & (signal.grid <= host.end)
        values = signal.vert_rms[span]
        if len(values) < int(VERT_SCAN_MIN_S / signal.dt):
            continue
        floor = float(np.percentile(values, 20))
        peak = float(np.percentile(values, 90))
        if peak <= max(floor, 1e-6) * VERT_CONTRAST_MIN:
            continue
        local = np.zeros(len(signal.grid), dtype=bool)
        local[span] = values >= floor + VERT_CONTRAST_RATIO * (peak - floor)
        result.extend(
            mask_segments(
                signal.grid,
                local,
                merge_gap=STAIR_MERGE_GAP_S,
                min_duration=VERT_SCAN_MIN_S,
                label="VERT",
            )
        )
    return sorted(result, key=lambda item: item.start)


def overlap_seconds(a: Segment, b: Segment) -> float:
    return max(0.0, min(a.end, b.end) - max(a.start, b.start))


def usable_bumps(runs: list[Segment], bumps: list[Segment]) -> list[Segment]:
    """剔除跨越多个楼梯段的鼓包：平台过短时上下楼的垂直鼓包会连成一片，无法归属。"""
    return [
        bump
        for bump in bumps
        if sum(1 for run in runs if overlap_seconds(bump, run) > VERT_MIN_OVERLAP_S) <= 1
    ]


def trustworthy_host(run: Segment, walking: list[Segment]) -> bool:
    """鼓包判据是否适用于该楼梯段：宿主行走段须含足够的非楼梯部分作平地基线。

    T02 的宿主被楼梯中途停顿切碎后几乎全是楼梯，段内没有平地可对比，鼓包不可信。
    """
    host = max(
        (item for item in walking if overlap_seconds(item, run) > 0.0),
        key=lambda item: overlap_seconds(item, run),
        default=None,
    )
    return host is not None and (host.end - host.start) - (run.end - run.start) >= VERT_HOST_LEVEL_S


def relocate_unsupported(
    signal: Signal,
    runs: list[Segment],
    walking: list[Segment],
    bumps: list[Segment],
    usable: list[Segment],
) -> tuple[list[Segment], list[str]]:
    """骨架硬约束的"必须去找"：协议槽位上的楼梯段若毫无垂直证据，改用同槽位的无主鼓包。

    位置由垂直证据定，标签由协议骨架定（决策 Q2/Q6）。槽位内找不到可用鼓包时**不虚构**，
    保留俯仰结果并给出阻断性 QA 警告，交人工裁决。
    "有无支撑"用全部鼓包判定（平台过短时上下楼鼓包相连，跨段鼓包同样是证据，
    只是无法归属到具体某段）；"用哪个鼓包顶替"只在可归属的 usable 中挑。
    """
    if not bumps:
        return runs, []
    claimed = {
        id(bump)
        for bump in usable
        if any(overlap_seconds(bump, run) > VERT_MIN_OVERLAP_S for run in runs)
    }
    result = list(runs)
    notes = []
    for index, run in enumerate(runs):
        if any(overlap_seconds(bump, run) > VERT_MIN_OVERLAP_S for bump in bumps):
            continue
        if not trustworthy_host(run, walking):
            continue
        previous = result[index - 1].end if index else 0.0
        following = result[index + 1].start if index + 1 < len(result) else float(signal.grid[-1])
        options = [
            bump
            for bump in usable
            if id(bump) not in claimed and bump.start >= previous and bump.end <= following
        ]
        label = "上楼" if run.label == "ASCENT" else "下楼"
        if not options:
            notes.append(
                f"[阻断] 协议要求的{label}段 {run.start:.1f}–{run.end:.1f}s 没有垂直加速度证据支撑，"
                f"槽位内也没有可用候选；已保留俯仰结果，须人工核定"
            )
            continue
        best = max(options, key=lambda item: item.end - item.start)
        claimed.add(id(best))
        result[index] = Segment(best.start, best.end, run.label)
        notes.append(
            f"{label}段 {run.start:.1f}–{run.end:.1f}s 无垂直证据支撑，"
            f"按同槽位鼓包重定位至 {best.start:.1f}–{best.end:.1f}s"
        )
    return result, notes


def host_asym_label(signal: Signal, host: Segment) -> str:
    """按垂直正负峰值比判定某行走段属上楼还是下楼；该比值与步速无关（ADR-0005）。"""
    mask = (signal.grid >= host.start) & (signal.grid <= host.end)
    if not mask.any():
        return "LEVEL"
    return "ASCENT" if float(np.median(signal.vert_asym[mask])) >= VERT_ASYM_SPLIT else "DESCENT"


def absorb_stair_hosts(
    signal: Signal, runs: list[Segment], walking: list[Segment]
) -> tuple[list[Segment], list[str]]:
    """吸收相邻的"无平地基线"行走段（ADR-0005）。

    楼梯中途停顿会把一次楼梯通过切成几个行走宿主；俯仰带只覆盖其中一段时，其余段被留成
    "平地行走"（P06_T02 实测多出两段，与采集描述"T02 全程只有上下楼与静止"矛盾）。
    只吸收自身没有平地基线（整段都像楼梯）、且垂直正负峰值比与目标段同类的相邻宿主。
    """
    result = list(runs)
    notes = []
    for host in walking:
        if any(overlap_seconds(host, run) > VERT_MIN_OVERLAP_S for run in result):
            continue
        if trustworthy_host(host, walking):
            continue  # 段内有平地基线，本就可能真是平地行走
        label = host_asym_label(signal, host)
        neighbours = [
            (index, run)
            for index, run in enumerate(result)
            if run.label == label
            and (0 <= host.start - run.end <= VERT_ABSORB_GAP_S or 0 <= run.start - host.end <= VERT_ABSORB_GAP_S)
        ]
        if not neighbours:
            continue
        index, run = min(
            neighbours,
            key=lambda item: min(abs(host.start - item[1].end), abs(item[1].start - host.end)),
        )
        merged = Segment(min(run.start, host.start), max(run.end, host.end), run.label)
        if any(
            other is not run and overlap_seconds(merged, other) > VERT_MIN_OVERLAP_S
            for other in result
        ):
            continue  # 吸收后会与另一楼梯段重叠，放弃
        result[index] = merged
        notes.append(
            f"行走段 {host.start:.1f}–{host.end:.1f}s 段内无平地基线、垂直峰值比判为"
            f"{'上楼' if label == 'ASCENT' else '下楼'}，已并入 {merged.start:.1f}–{merged.end:.1f}s"
        )
    return result, notes


def adjudicate_terrain(signal: Signal, runs: list[Segment], walking: list[Segment]) -> tuple[list[Segment], list[str]]:
    """用垂直加速度鼓包裁决下楼段的边界（ADR-0005）。

    三条准入，均由 7.21 上的失败案例反推：
    1. 只裁决 DESCENT——上楼的俯仰对比在 7.21/7.27 一律充分，失准的一律是下楼；
    2. 宿主行走段须含 ≥VERT_HOST_LEVEL_S 的非楼梯部分——T02 的宿主被楼梯中途停顿切碎后
       几乎全是楼梯，段内没有平地基线，鼓包会误切段首（P04_T02、P05_T02 实测）；
    3. 只允许收窄不允许外扩——下楼结束后平地的垂直 RMS 是软衰减，鼓包尾沿会吃进平地
       （P04_T01 实测外扩 3.8s）；收窄方向在 7.21/7.27 上全部正确。
    """
    bumps = vertical_bumps(signal, walking)
    if not bumps:
        return runs, []
    usable = usable_bumps(runs, bumps)
    runs, notes = relocate_unsupported(signal, runs, walking, bumps, usable)
    result = list(runs)
    for index, run in enumerate(runs):
        if run.label != "DESCENT" or not trustworthy_host(run, walking):
            continue
        best = max(usable, key=lambda item: overlap_seconds(item, run), default=None)
        if best is None or overlap_seconds(best, run) <= VERT_MIN_OVERLAP_S:
            continue
        start = best.start if best.start - run.start >= VERT_REFIT_MIN_S else run.start
        end = best.end if run.end - best.end >= VERT_REFIT_MIN_S else run.end
        if end - start < STAIR_MIN_S or (start, end) == (run.start, run.end):
            continue
        result[index] = Segment(start, end, run.label)
        notes.append(
            f"下楼段 {run.start:.1f}–{run.end:.1f}s 按垂直加速度证据收窄为 {start:.1f}–{end:.1f}s"
        )
    return result, notes


def terrain_runs(signal: Signal, walking: list[Segment]) -> tuple[list[Segment], float | None, str | None]:
    pitch = uniform_filter1d(signal.pitch, PITCH_SMOOTH_N, mode="nearest")
    active_mask = np.zeros(len(signal.grid), dtype=bool)
    for segment in walking:
        active_mask |= (signal.grid >= segment.start) & (signal.grid <= segment.end)
    values = pitch[active_mask]
    if len(values) < 100:
        return [], None, "行走样本不足，无法估计俯仰带"
    centers = ordered_pitch_centers(values, (20, 50, 80))
    if min(np.diff(centers)) < PITCH_MIN_SEPARATION:
        centers = ordered_pitch_centers(values, (5, 50, 95))
    if min(np.diff(centers)) < PITCH_MIN_SEPARATION:
        return [], None, "俯仰带无法分离为下楼/平地/上楼"
    lower, upper = (centers[:-1] + centers[1:]) / 2
    runs = []
    for label, mask in (
        ("DESCENT", active_mask & (pitch < lower)),
        ("ASCENT", active_mask & (pitch > upper)),
    ):
        runs.extend(
            mask_segments(
                signal.grid,
                mask,
                merge_gap=STAIR_MERGE_GAP_S,
                min_duration=STAIR_MIN_S,
                label=label,
            )
        )
    ordered = sorted(runs, key=lambda item: item.start)
    merged = []
    for run in ordered:
        if merged and run.label == merged[-1].label and run.start - merged[-1].end < STAIR_JOIN_GAP_S:
            merged[-1] = Segment(merged[-1].start, run.end, run.label)
        else:
            merged.append(run)
    return merged, float(centers[1]), None


def filter_terrain(trial: str, runs: list[Segment]) -> tuple[list[Segment], str | None]:
    if trial == "T02":
        merged = []
        for run in runs:
            if merged and run.label == merged[-1].label and run.start - merged[-1].end <= 6.0:
                merged[-1] = Segment(merged[-1].start, run.end, run.label)
            else:
                merged.append(run)
        runs = merged
    expected = {"T01": "ADADAD", "T02": "ADAD", "T04": "ADAD"}.get(trial)
    if not expected:
        return ([], "T03 检测到楼梯段，已忽略") if trial == "T03" and runs else (runs, None)
    labels = {"A": "ASCENT", "D": "DESCENT"}
    choices = []
    for indexes in combinations(range(len(runs)), len(expected)):
        selected = [runs[index] for index in indexes]
        if [run.label for run in selected] != [labels[value] for value in expected]:
            continue
        score = sum(min(run.end - run.start, 12.0) for run in selected)
        choices.append((score, selected))
    if choices:
        best = max(choices, key=lambda item: item[0])[1]
        kept = {(run.start, run.end) for run in best}
        dropped = [run for run in runs if (run.start, run.end) not in kept]
        significant = [run for run in dropped if run.end - run.start >= 3.0]
        flag = (
            f"提示：丢弃 {len(significant)} 段时长≥3s 的楼梯候选（俯仰带内可能存在协议外地形变化）"
            if significant
            else None
        )
        return best, flag
    return runs, f"楼梯段序列与协议 {expected} 不符（检测到 {''.join(r.label[0] for r in runs) or '无'}）"


def first_crossing(
    grid: np.ndarray,
    pitch: np.ndarray,
    half: float,
    sign: float,
    start: float,
    end: float,
    toward: bool,
    hold_s: float = 1.0,
    hold_fraction: float = 0.7,
) -> float | None:
    """首个"持续性"穿越：穿越后 hold_s 内须有 hold_fraction 停留在目标侧（排除步态俯仰抖动的假穿越）。"""
    window = (grid >= start) & (grid <= end)
    if not window.any():
        return None
    values = (pitch[window] - half) * sign
    side = values >= 0 if toward else values <= 0
    crossings = np.flatnonzero(np.diff(side.astype(np.int8)) == 1)
    if not len(crossings):
        return None
    hold_n = max(1, int(round(hold_s / (grid[1] - grid[0]))))
    for index in crossings:
        segment = side[index + 1 : index + 1 + hold_n]
        if len(segment) and segment.mean() >= hold_fraction:
            return float(grid[window][index + 1])
    return None


def trim_run_by_impact(signal: Signal, run: Segment) -> tuple[Segment, bool, bool]:
    """按落脚冲击修剪楼梯段边缘：俯仰带弱分离（如 P05 下楼 ≈ 平地）时段首/尾会渗入平地行走。"""
    mask = (signal.grid >= run.start) & (signal.grid <= run.end)
    indexes = np.flatnonzero(mask)
    if len(indexes) < int(3.0 / signal.dt):
        return run, False, False
    values = signal.impact[indexes]
    quarter = len(values) // 4
    core = float(np.percentile(values[quarter : len(values) - quarter], 75)) if len(values) > 8 else float(np.median(values))
    if core <= 0:
        return run, False, False
    # 低冲击掩码再作 1.5s 平滑：步态周期内的冲击振荡会打断连续性判定
    smoothed = uniform_filter1d(values, 150, mode="nearest")
    low = smoothed < IMPACT_LOW_RATIO * core
    start, end = run.start, run.end
    start_trimmed = end_trimmed = False
    lead = int(np.argmax(~low)) if low[0] else 0
    if lead * signal.dt >= IMPACT_TRIM_MIN_S:
        start = float(signal.grid[indexes[lead]])
        start_trimmed = True
    tail = int(np.argmax(~low[::-1])) if low[-1] else 0
    if tail * signal.dt >= IMPACT_TRIM_MIN_S:
        end = float(signal.grid[indexes[len(values) - 1 - tail]])
        end_trimmed = True
    if end - start < 1.5:
        return run, False, False
    return Segment(start, end, run.label), start_trimmed, end_trimmed


def align_stairs(
    signal: Signal,
    runs: list[Segment],
    walking: list[Segment],
    gait: list[Step],
    level_center: float,
) -> tuple[list[Segment], list[Step]]:
    band = uniform_filter1d(signal.pitch, PITCH_SMOOTH_N, mode="nearest")
    pitch = uniform_filter1d(signal.pitch, PITCH_CROSS_SMOOTH_N, mode="nearest")
    aligned = []
    entry_steps = []
    confirmations = []
    for run in runs:
        run, start_trimmed, end_trimmed = trim_run_by_impact(signal, run)
        run_mask = (signal.grid >= run.start) & (signal.grid <= run.end)
        run_med = float(np.median(band[run_mask]))
        sign = 1.0 if run_med >= level_center else -1.0
        half = (level_center + run_med) / 2
        run_impact_core = float(np.percentile(signal.impact[run_mask], 75)) if run_mask.any() else 0.0
        weak_pitch = abs(run_med - level_center) < WEAK_PITCH_DELTA

        if start_trimmed:
            anchor = run.start  # 冲击修剪过的边比俯仰穿越可靠
        elif weak_pitch and run_impact_core > 0:
            rise = first_crossing(
                signal.grid,
                signal.impact,
                IMPACT_ANCHOR_RATIO * run_impact_core,
                1.0,
                run.start - ENTRY_SEARCH_BACK_S,
                run.start + 2.0,
                True,
            )
            anchor = (rise - ENTRY_CROSS_LEAD_S) if rise is not None else run.start
        else:
            cross_in = first_crossing(
                signal.grid, pitch, half, sign, run.start - ENTRY_SEARCH_BACK_S, run.start + ENTRY_SEARCH_FWD_S, True
            )
            anchor = (cross_in - ENTRY_CROSS_LEAD_S) if cross_in is not None else run.start
        candidates = [step for step in gait if anchor - 1.5 <= step.onset <= anchor + 1.2]
        entry_step = None
        if candidates:
            entry_step = min(candidates, key=lambda step: abs(step.onset - anchor))
            start = entry_step.onset + STEP_ONSET_BIAS_S
        else:
            start = anchor

        if end_trimmed:
            out_anchor = run.end
        elif weak_pitch and run_impact_core > 0:
            fall = first_crossing(
                signal.grid,
                signal.impact,
                IMPACT_ANCHOR_RATIO * run_impact_core,
                -1.0,
                max(start + 3.0, run.end - 4.5),
                run.end + EXIT_SEARCH_FWD_S,
                True,
                hold_s=IMPACT_EXIT_HOLD_S,
                hold_fraction=0.75,
            )
            out_anchor = fall if fall is not None else run.end
        else:
            cross_out = first_crossing(
                signal.grid, pitch, half, sign, run.end - EXIT_SEARCH_BACK_S, run.end + EXIT_SEARCH_FWD_S, False,
                hold_s=2.0, hold_fraction=0.85,
            )
            out_anchor = cross_out if cross_out is not None else run.end
        after = [step for step in gait if out_anchor - 1.2 <= step.onset <= out_anchor + 1.2]
        if after:
            exit_step = min(after, key=lambda step: abs(step.onset - out_anchor))
            if abs(exit_step.onset - out_anchor) <= EXIT_ONSET_MAX_GAP_S:
                end = exit_step.onset + STEP_ONSET_BIAS_S
            else:
                end = out_anchor
        else:
            end = out_anchor

        # 步频出口修剪：俯仰与冲击都缺乏对比时（如 P05 下楼接快走），
        # 楼梯步频（间隔 ~0.87s）显著慢于随后的快走（~0.59s）；须此后全部保持快步频
        inside = [step for step in gait if start <= step.onset <= end]
        if len(inside) >= 8:
            intervals = np.diff([step.onset for step in inside])
            core_count = max(4, int(len(intervals) * 0.6))
            core = float(np.median(intervals[:core_count]))
            for k in range(core_count, len(intervals) - 2):
                remaining = intervals[k : k + 5]
                if len(remaining) >= 3 and all(value < CADENCE_EXIT_RATIO * core for value in remaining):
                    candidate = inside[k].onset + STEP_ONSET_BIAS_S
                    if end - candidate > 1.0:
                        end = candidate
                    break

        host = [item for item in walking if item.start <= run.start <= item.end or item.start <= run.end <= item.end]
        if host:
            host_start = min(item.start for item in host)
            host_end = max(item.end for item in host)
            # 站立起步即上/下楼：宿主行走段起点紧贴楼梯段，且起步不是原地转身
            # （平台转身后再下楼的场景以 gyroY 爆发甄别；下楼首步轻落，冲击判据不可用）
            if run.start - host_start <= 3.5:
                early = [step for step in gait if host_start - 0.2 <= step.onset <= host_start + 0.8]
                if early:
                    probe = early[0]
                    turn_mask = (signal.grid >= host_start) & (signal.grid <= host_start + 1.4)
                    turn_peak = (
                        float(np.max(np.abs(uniform_filter1d(signal.gyro[:, 1], TURN_WINDOW_N, mode="nearest")[turn_mask])))
                        if turn_mask.any()
                        else 0.0
                    )
                    earlier_than_entry = entry_step is None or probe.onset < entry_step.onset - 0.3
                    if turn_peak < TURN_GYRO_Y and earlier_than_entry:
                        entry_step = probe
                        start = probe.onset + STEP_ONSET_BIAS_S
            # 登顶即停：上楼至平台随即长静止（≥2.5s），顶部数步俯仰与冲击都趋平，但仍是楼梯步态
            following = [item.start for item in walking if item.start > host_end - 0.1]
            still_after = (min(following) - host_end) if following else float("inf")
            if run.label == "ASCENT" and 0.0 <= host_end - run.end <= 3.5 and host_end - end <= 4.5 and still_after >= 2.5:
                end = host_end
            # 到达即停：计算出口已贴近宿主行走结束沿（其后随即静止），吸附到结束沿
            elif end >= host_end - 0.45:
                end = host_end
            start = max(start, host_start)
            end = min(end, host_end)
        if end <= start + 1.0:
            continue
        aligned.append(Segment(start, end, run.label))
        entry_steps.append(entry_step)

    # 平台挤压保护：相邻楼梯段间隙 < MIN_PLATFORM_S 时，迭代尝试"出口回退一步"或
    # "入口前进一步"，以候选平台窗内落脚冲击均值更低（平台应安静）者为准。
    def window_impact(a: float, b: float) -> float:
        mask = (signal.grid >= a) & (signal.grid <= b)
        return float(np.mean(signal.impact[mask])) if mask.any() else float("inf")

    for index in range(1, len(aligned)):
        for _ in range(4):
            previous, current = aligned[index - 1], aligned[index]
            if current.start - previous.end >= MIN_PLATFORM_S:
                break
            retreat_candidates = [s for s in gait if s.onset + STEP_ONSET_BIAS_S < previous.end - 0.2]
            advance_candidates = [s for s in gait if s.onset + STEP_ONSET_BIAS_S > current.start + 0.2]
            options = []
            if retreat_candidates:
                new_end = retreat_candidates[-1].onset + STEP_ONSET_BIAS_S
                if new_end > previous.start + 1.0:
                    options.append(("retreat", new_end, window_impact(new_end, current.start)))
            if advance_candidates:
                new_start = advance_candidates[0].onset + STEP_ONSET_BIAS_S
                if new_start < current.end - 1.0:
                    options.append(("advance", new_start, window_impact(previous.end, new_start)))
            if not options:
                break
            kind, value, _ = min(options, key=lambda item: item[2])
            if kind == "retreat":
                aligned[index - 1] = Segment(previous.start, value, previous.label)
            else:
                aligned[index] = Segment(value, current.end, current.label)

    for segment, entry_step in zip(aligned, entry_steps):
        if entry_step is None:
            continue
        following = [
            step
            for step in gait
            if step.leg != entry_step.leg
            and entry_step.onset + CONFIRM_MIN_S <= step.onset <= entry_step.onset + CONFIRM_MAX_S
        ]
        if following:
            confirmations.append(
                Step(following[0].onset + STEP_ONSET_BIAS_S, following[0].peak, following[0].leg, following[0].prominence)
            )
    return aligned, confirmations


def stair_pauses(signal: Signal, stairs: list[Segment]) -> list[Segment]:
    quiet = (signal.leg_speed < PAUSE_LEG_SPEED) & (signal.local_motion < max(signal.local_threshold, 6.0))
    pauses = []
    for run in stairs:
        lo, hi = run.start + PAUSE_GUARD_S, run.end - PAUSE_GUARD_S
        if hi - lo < PAUSE_MIN_S:
            continue
        window = (signal.grid >= lo) & (signal.grid <= hi)
        mask = np.zeros(len(signal.grid), dtype=bool)
        mask[window] = quiet[window]
        for pause in mask_segments(signal.grid, mask, merge_gap=PAUSE_MERGE_GAP_S, min_duration=PAUSE_MIN_S, label="STILL"):
            if any(pause.start < gap_end and pause.end > gap_start for gap_start, gap_end in signal.gaps):
                continue  # 断档内的插值平坦线不是停顿
            span = (signal.grid >= pause.start) & (signal.grid <= pause.end)
            if float(np.percentile(signal.leg_speed[span], 25)) < PAUSE_DEPTH_SPEED:
                pauses.append(pause)
    return pauses


def state_at(segments: list[Segment], seconds: float, default: str) -> str:
    matches = [item for item in segments if item.start <= seconds < item.end]
    return matches[-1].label if matches else default


def annotate_file(path: Path, signal: Signal | None = None) -> tuple[list[Event], dict]:
    match = FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"无法从文件名识别 session/trial：{path.name}")
    session, trial = (value.upper() for value in match.groups())
    signal = signal or Signal(path)
    gait = steps(signal)
    activities, flags = activity_segments(signal, gait)

    walking = [item for item in activities if item.label == "WALKING"]
    terrain: list[Segment] = []
    confirmations: list[Step] = []
    pauses: list[Segment] = []
    if walking:
        runs, level_center, terrain_flag = terrain_runs(signal, walking)
        if terrain_flag and trial != "T03":
            flags.append(terrain_flag)
        if runs:
            runs, filter_flag = filter_terrain(trial, runs)
            if filter_flag:
                flags.append(filter_flag)
            runs, vertical_notes = adjudicate_terrain(signal, runs, walking)
            # "[阻断]" 开头的裁决结论须计入退出码，其余是可追溯的改写记录，属提示级
            flags.extend(note if note.startswith("[阻断]") else f"提示：{note}" for note in vertical_notes)
            terrain, confirmations = align_stairs(signal, runs, walking, gait, level_center)
            pauses = stair_pauses(signal, terrain)

    boundaries = {0.0}
    for item in (*activities, *terrain, *pauses):
        boundaries.update((item.start, item.end))
    ordered = sorted(value for value in boundaries if 0.0 <= value <= signal.grid[-1])

    overlay = [*activities, *pauses]  # state_at 取最后匹配：停顿覆盖行走
    timeline = []
    previous = None
    for seconds in ordered:
        activity = state_at(overlay, seconds + signal.dt, "STILL")
        terrain_label = state_at(terrain, seconds + signal.dt, "LEVEL")
        state = (activity, terrain_label)
        if state == previous:
            continue
        timeline.append((seconds, state))
        previous = state

    collapsed = list(timeline)
    changed = True
    while changed:
        changed = False
        for index in range(1, len(collapsed)):
            end = collapsed[index + 1][0] if index + 1 < len(collapsed) else float(signal.grid[-1])
            if collapsed[index][1] == ("WALKING", "LEVEL") and end - collapsed[index][0] < WALK_SLIVER_S:
                del collapsed[index]
                changed = True
                break
        if changed:
            continue
        for index in range(1, len(collapsed)):
            if collapsed[index][1] == collapsed[index - 1][1]:
                del collapsed[index]
                changed = True
                break

    rows = []
    for seconds, (activity, terrain_label) in collapsed:
        note = "近似：自动检测的文件开头静止" if seconds == 0 else "近似：自动检测的组合状态边界"
        rows.append(Event(session, trial, path.name, signal.snap(seconds), activity, terrain_label, note))

    for step in confirmations:
        terrain_label = state_at(terrain, step.onset + signal.dt, "LEVEL")
        if terrain_label == "LEVEL":
            continue
        activity = state_at(overlay, step.onset + signal.dt, "STILL")
        rows.append(
            Event(session, trial, path.name, signal.snap(step.onset), activity, terrain_label, "近似：自动检测的楼梯第二步确认")
        )

    if collapsed:
        tail_seconds, tail_state = collapsed[-1]
        preceding = [item for item in activities if item.end <= tail_seconds + 0.5]
        last_activity = preceding[-1].label if preceding else None
        if (
            tail_state == ("STILL", "LEVEL")
            and last_activity is not None
            and last_activity != "WALKING"
            and signal.grid[-1] - tail_seconds >= TAIL_CONFIRM_GAP_S
        ):
            rows.append(
                Event(
                    session,
                    trial,
                    path.name,
                    signal.snap(signal.grid[-1] - TAIL_CONFIRM_OFFSET_S),
                    "STILL",
                    "LEVEL",
                    "近似：自动检测的文件结束静止确认",
                )
            )

    events = sorted(rows, key=lambda item: parse_timestamp(item.timestamp))
    detail = {
        "calibration": signal.calibration,
        "flags": flags,
        "gaps": [[round(a, 2), round(b, 2)] for a, b in signal.gaps],
    }
    return events, detail


# ---- QA：协议合理性检查（只标记，不改写输出） ----


def qa_check(trial: str, events: list[Event], detail: dict) -> list[str]:
    flags = list(detail["flags"])
    if detail["gaps"]:
        worst = max(b - a for a, b in detail["gaps"])
        flags.append(f"数据断档 {len(detail['gaps'])} 处，最长 {worst:.1f}s")
    if not events:
        return flags + ["没有生成任何事件"]

    states = []
    for event in events:
        state = (event.activity_truth, event.terrain_truth)
        if not states or states[-1][1] != state:
            states.append((event, state))
    activities = {state[0] for _, state in states}
    stair_letters = ""
    previous_terrain = "LEVEL"
    for _, (activity, terrain) in states:
        if terrain != previous_terrain and terrain in {"ASCENT", "DESCENT"}:
            stair_letters += terrain[0]
        previous_terrain = terrain

    first = events[0]
    if (first.activity_truth, first.terrain_truth) != ("STILL", "LEVEL"):
        flags.append("首事件不是文件开头静止")
    last = events[-1]
    if (last.activity_truth, last.terrain_truth) != ("STILL", "LEVEL"):
        flags.append("末状态不是静止·平地")

    expected_stairs = {"T01": "ADADAD", "T02": "ADAD", "T04": "ADAD"}.get(trial, "")
    if stair_letters != expected_stairs:
        flags.append(f"楼梯序列 {stair_letters or '无'} 与协议 {expected_stairs or '无'} 不符")
    off_level_pause = [
        1 for _, (activity, terrain) in states if activity == "STILL" and terrain != "LEVEL"
    ]
    if trial == "T02":
        if len(off_level_pause) != 4:
            flags.append(f"楼梯中途停顿数 {len(off_level_pause)} 应为 4")
    elif off_level_pause:
        flags.append(f"{trial} 出现 {len(off_level_pause)} 次楼梯中途静止（协议不应有）")

    if trial in {"T01", "T02"}:
        unexpected = activities - {"STILL", "WALKING", "OTHER"}
        if unexpected:
            flags.append(f"出现协议外活动标签：{sorted(unexpected)}")
    if trial == "T03":
        expected_families = {
            "BEND": 1,
            "SQUAT": 1,
            "HIGH_KNEE_SINGLE": 2,
            "HIGH_KNEE_ALTERNATING": 1,
            "TURNING_LEFT": 1,
            "TURNING_RIGHT": 1,
            "SHUFFLE": 1,
        }
        counts = {}
        for _, (activity, _terrain) in states:
            counts[activity] = counts.get(activity, 0) + 1
        for family, minimum in expected_families.items():
            if counts.get(family, 0) < minimum:
                flags.append(f"缺少动作族 {family}（检测 {counts.get(family, 0)} < 期望 {minimum}）")
        if "WALKING" in activities:
            flags.append("T03 出现 WALKING（协议为原地动作）")
    if trial == "T04":
        for family in ("SQUAT", "HIGH_KNEE_ALTERNATING"):
            if family not in activities:
                flags.append(f"缺少动作族 {family}")
    return flags


def input_files(directory: Path, subject: str | None) -> list[Path]:
    files = []
    for path in directory.glob("*.csv"):
        match = FILE_RE.match(path.name)
        if match and (subject is None or match.group(1).upper().startswith(subject.upper())):
            files.append(path)
    if not files:
        raise ValueError(f"{directory} 中没有匹配的原始试次 CSV")
    return sorted(files, key=lambda path: FILE_RE.match(path.name).groups())


def write_events(path: Path, events: list[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(asdict(event) for event in events)


def read_reference(path: Path, subject: str | None) -> dict[str, list[Event]]:
    grouped = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(OUTPUT_FIELDS).issubset(reader.fieldnames):
            raise ValueError(f"参考文件字段不完整：{path}")
        for row in reader:
            if subject and not row["session_id"].upper().startswith(subject.upper()):
                continue
            event = Event(*(row[field].strip() for field in OUTPUT_FIELDS))
            grouped.setdefault(event.input_file, []).append(event)
    return grouped


def validate(events: list[Event], reference_path: Path, subject: str | None) -> dict:
    actual = {}
    for event in events:
        actual.setdefault(event.input_file, []).append(event)
    reference = read_reference(reference_path, subject)
    errors = []
    files = {}
    sequence_ok = True
    for name in sorted(set(actual) | set(reference)):
        got, expected = actual.get(name, []), reference.get(name, [])
        got_states = [(item.activity_truth, item.terrain_truth) for item in got]
        expected_states = [(item.activity_truth, item.terrain_truth) for item in expected]
        same_sequence = got_states == expected_states
        sequence_ok &= same_sequence
        file_errors = []
        if same_sequence:
            file_errors = [
                timestamp_delta_seconds(left.timestamp, right.timestamp)
                for left, right in zip(got, expected)
            ]
            errors.extend(file_errors)
        files[name] = {
            "generated_events": len(got),
            "reference_events": len(expected),
            "sequence_ok": same_sequence,
            "median_seconds": median(file_errors) if file_errors else None,
            "max_seconds": max(file_errors) if file_errors else None,
        }
    metrics = {
        "sequence_ok": sequence_ok,
        "matched_events": len(errors),
        "median_seconds": float(np.median(errors)) if errors else None,
        "p95_seconds": float(np.percentile(errors, 95)) if errors else None,
        "max_seconds": max(errors) if errors else None,
    }
    metrics["passed"] = bool(
        sequence_ok
        and errors
        and metrics["median_seconds"] <= VALIDATION_THRESHOLDS["median"]
        and metrics["p95_seconds"] <= VALIDATION_THRESHOLDS["p95"]
        and metrics["max_seconds"] <= VALIDATION_THRESHOLDS["max"]
    )
    return {"thresholds_seconds": dict(VALIDATION_THRESHOLDS), "metrics": metrics, "files": files}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_dir", type=Path, help="包含原始 T01-T04 CSV 的目录")
    parser.add_argument("--output", type=Path, help="输出 CSV；默认 INPUT_DIR/[SUBJECT_]state_changes.auto.csv")
    parser.add_argument("--subject", help="只处理指定受试者，例如 P04")
    parser.add_argument("--reference", type=Path, help="可选参考标注，仅用于验证")
    parser.add_argument("--report", type=Path, help="QA/验证报告 JSON；默认 OUTPUT.report.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    default_name = f"{args.subject.upper()}_state_changes.auto.csv" if args.subject else "state_changes.auto.csv"
    output = args.output or args.input_dir / default_name
    if args.reference and output.resolve() == args.reference.resolve():
        raise ValueError("输出路径不能覆盖参考标注")

    events: list[Event] = []
    qa_files = {}
    qa_passed = True
    for path in input_files(args.input_dir, args.subject):
        file_events, detail = annotate_file(path)
        events.extend(file_events)
        flags = qa_check(FILE_RE.match(path.name).group(2).upper(), file_events, detail)
        qa_files[path.name] = {
            "events": len(file_events),
            "flags": flags,
            "calibration": detail["calibration"],
            "gaps": detail["gaps"],
        }
        blocking = [flag for flag in flags if not flag.startswith("提示：")]
        if blocking:
            qa_passed = False
        for flag in flags:
            print(f"[QA] {path.name}: {flag}", file=sys.stderr)

    write_events(output, events)
    print(f"已写入 {len(events)} 条自动标注：{output}")

    report = {"qa": {"passed": qa_passed, "files": qa_files}, "validation": None}
    if args.reference:
        report["validation"] = validate(events, args.reference, args.subject)
        metrics = report["validation"]["metrics"]
        print(
            "验证：sequence_ok={sequence_ok} median={median_seconds} "
            "p95={p95_seconds} max={max_seconds} passed={passed}".format(**metrics)
        )
    report_path = args.report or output.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"QA：passed={qa_passed} 报告：{report_path}")

    validation_ok = report["validation"] is None or report["validation"]["metrics"]["passed"]
    return 0 if qa_passed and validation_ok else 1


def cli() -> int:
    try:
        return main()
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
