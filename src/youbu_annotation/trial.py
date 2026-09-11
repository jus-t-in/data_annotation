"""Strict loading and normalized signal views for one protocol trial."""

from __future__ import annotations

import csv
import hashlib
import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import savgol_filter

from . import auto_annotate as legacy


class TrialValidationError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass(frozen=True)
class DataGap:
    before_index: int
    after_index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class TrialFileInfo:
    """Protocol role inferred from the exact input filename contract."""

    filename: str
    mode: str
    role: str
    session_id: str | None = None
    trial_id: str | None = None
    part: str | None = None
    reason: str = ""

    @property
    def is_v2(self) -> bool:
        return self.mode == "v2"

    @property
    def is_v3(self) -> bool:
        return self.mode == "v3"

    @property
    def is_formal(self) -> bool:
        return self.role == "formal"

    @property
    def is_source(self) -> bool:
        return self.role == "source"

    @property
    def supported(self) -> bool:
        return self.role in {"formal", "source"}

    @property
    def editable(self) -> bool:
        return self.is_formal

    @property
    def is_formal_v3(self) -> bool:
        return self.is_v3 and self.is_formal

    @property
    def is_read_only(self) -> bool:
        return self.is_source or self.role == "unsupported"

    @property
    def is_merged(self) -> bool:
        return self.is_v3 and self.trial_id == "T04" and self.part == "1+2"

    @property
    def expected_video_count(self) -> int:
        if not self.is_v3 or not self.is_formal:
            return 0
        return 2 if self.is_merged else 1

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "mode": self.mode,
            "role": self.role,
            "session_id": self.session_id,
            "trial_id": self.trial_id,
            "part": self.part,
            "reason": self.reason,
            "read_only": self.is_read_only,
            "supported": self.supported,
            "editable": self.editable,
            "expected_video_count": self.expected_video_count,
        }


_V3_FILE_RE = re.compile(
    r"^(?P<session>P\d+_S\d+)_(?P<trial>T0[1-6])_v3(?:_(?P<part>1\+2|1|2))?\.csv$",
    re.IGNORECASE,
)


def identify_trial_file(filename: str | Path) -> TrialFileInfo:
    name = Path(filename).name
    match = _V3_FILE_RE.fullmatch(name)
    if match:
        session_id = match.group("session").upper()
        trial_id = match.group("trial").upper()
        part = match.group("part")
        if trial_id == "T04" and part == "1+2":
            role = "formal"
        elif trial_id == "T04" and part in {"1", "2"}:
            role = "source"
        elif trial_id == "T04":
            role = "unsupported"
        elif part is None:
            role = "formal"
        else:
            role = "unsupported"
        return TrialFileInfo(name, "v3", role, session_id, trial_id, part)

    legacy_match = legacy.FILE_RE.match(name)
    if legacy_match and "_v3" not in name.casefold():
        return TrialFileInfo(
            name,
            "v2",
            "formal",
            legacy_match.group(1).upper(),
            legacy_match.group(2).upper(),
        )
    reason = "V3 文件名不符合标准角色后缀" if "_v3" in name.casefold() else "无法从文件名判断协议版本"
    return TrialFileInfo(name, "unknown", "unknown", reason=reason)


identify_file_role = identify_trial_file


@dataclass(frozen=True)
class GapContract:
    data_gaps: tuple[DataGap, ...]
    stitch_gap: DataGap | None
    errors: tuple[str, ...] = ()

    @property
    def gaps(self) -> tuple[DataGap, ...]:
        return self.data_gaps

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "data_gaps": [vars(gap) for gap in self.data_gaps],
            "stitch_gap": vars(self.stitch_gap) if self.stitch_gap else None,
            "errors": list(self.errors),
            "valid": self.valid,
        }


InputGapContract = GapContract


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _smooth(values: np.ndarray, preferred: int = 11, order: int = 3) -> np.ndarray:
    size = len(values)
    window = min(preferred, size if size % 2 else size - 1)
    if window <= order:
        return values.copy()
    return savgol_filter(values, window, order)


@dataclass
class TrialData:
    path: Path
    session_id: str
    trial_id: str
    time_column: str
    seconds: np.ndarray
    timestamps: list[str]
    start_datetime: datetime | None
    channels: dict[str, np.ndarray]
    ignored_columns: tuple[str, ...]
    duplicate_rows: int
    gaps: list[DataGap]
    warnings: list[str]
    source_hash: str

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        session_id: str | None = None,
        trial_id: str | None = None,
    ) -> "TrialData":
        path = path.resolve()
        match = _V3_FILE_RE.fullmatch(path.name) or legacy.FILE_RE.match(path.name)
        if not match and (not session_id or not trial_id):
            raise TrialValidationError([f"文件名不符合试次协议：{path.name}"])
        try:
            with path.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                fields = tuple(reader.fieldnames or ())
                if len(fields) <= 1 and fields and (";" in fields[0] or "\t" in fields[0]):
                    raise TrialValidationError(["CSV 必须使用英文逗号分隔"])
                rows = list(reader)
        except UnicodeDecodeError as exc:
            raise TrialValidationError(["文件不是有效的 UTF-8/UTF-8 BOM 编码"]) from exc
        if not fields:
            raise TrialValidationError(["CSV 没有表头"])
        if any(not name.strip() for name in fields):
            raise TrialValidationError(["CSV 表头包含空字段名"])
        if len(set(fields)) != len(fields):
            raise TrialValidationError(["CSV 表头包含重复字段名"])
        if not rows:
            raise TrialValidationError(["CSV 没有数据行"])

        time_columns = [name for name in (legacy.TIME_COLUMN, legacy.ELAPSED_COLUMN) if name in fields]
        left_column = next((name for name in (legacy.LEFT_COLUMN, legacy.LEFT_COLUMN_ALT) if name in fields), None)
        required = {legacy.RIGHT_COLUMN, legacy.PITCH_COLUMN, *legacy.GYRO_COLUMNS, *legacy.ACCEL_COLUMNS}
        missing = sorted(required - set(fields))
        if not time_columns:
            missing.append(f"{legacy.TIME_COLUMN} 或 {legacy.ELAPSED_COLUMN}")
        if left_column is None:
            missing.append(f"{legacy.LEFT_COLUMN} 或 {legacy.LEFT_COLUMN_ALT}")
        if missing:
            raise TrialValidationError([f"缺少字段：{', '.join(missing)}"])

        problems: list[str] = []
        absolute: list[datetime] | None = [] if legacy.TIME_COLUMN in time_columns else None
        elapsed: list[float] | None = [] if legacy.ELAPSED_COLUMN in time_columns else None
        numeric_required = [*legacy.ACCEL_COLUMNS, *legacy.GYRO_COLUMNS, legacy.PITCH_COLUMN, legacy.RIGHT_COLUMN, left_column]
        raw_numeric = {name: [] for name in numeric_required}
        for line, row in enumerate(rows, 2):
            if absolute is not None:
                try:
                    absolute.append(datetime.strptime((row.get(legacy.TIME_COLUMN) or "").strip(), legacy.TIME_FORMAT))
                except ValueError:
                    problems.append(f"第 {line} 行 {legacy.TIME_COLUMN} 无效：{row.get(legacy.TIME_COLUMN)!r}")
            if elapsed is not None:
                try:
                    value = float((row.get(legacy.ELAPSED_COLUMN) or "").strip())
                    if not math.isfinite(value):
                        raise ValueError
                    elapsed.append(value)
                except ValueError:
                    problems.append(f"第 {line} 行 {legacy.ELAPSED_COLUMN} 无效：{row.get(legacy.ELAPSED_COLUMN)!r}")
            for name in numeric_required:
                try:
                    value = float((row.get(name) or "").strip())
                    if not math.isfinite(value):
                        raise ValueError
                    raw_numeric[name].append(value)
                except ValueError:
                    problems.append(f"第 {line} 行 {name} 无效：{row.get(name)!r}")
            if len(problems) >= 1000:
                problems.append("错误超过 1000 条，已停止继续列举")
                break
        if problems:
            raise TrialValidationError(problems)

        if absolute is not None:
            first = min(absolute)
            raw_seconds = np.array([(value - first).total_seconds() for value in absolute], dtype=float)
            time_column = legacy.TIME_COLUMN
            raw_stamps = [(row[legacy.TIME_COLUMN] or "").strip() for row in rows]
            start_datetime = first
            if elapsed is not None:
                relative = np.asarray(elapsed, dtype=float) - min(elapsed)
                if not np.allclose(raw_seconds, relative, atol=0.01, rtol=0):
                    raise TrialValidationError(["RX Date/Time 与 Elapsed (s) 的时间差不一致"])
        else:
            raw_elapsed = np.asarray(elapsed, dtype=float)
            raw_seconds = raw_elapsed - raw_elapsed.min()
            time_column = legacy.ELAPSED_COLUMN
            raw_stamps = [(row[legacy.ELAPSED_COLUMN] or "").strip() for row in rows]
            start_datetime = None

        seconds, inverse = np.unique(raw_seconds, return_inverse=True)
        if len(seconds) < 2:
            raise TrialValidationError(["试次至少需要两个不同时间的采样点"])
        counts = np.bincount(inverse)
        first_indices = np.full(len(seconds), len(rows), dtype=int)
        np.minimum.at(first_indices, inverse, np.arange(len(rows)))
        timestamps = [raw_stamps[int(index)] for index in first_indices]

        channels: dict[str, np.ndarray] = {}
        for name, values in raw_numeric.items():
            channels[name] = np.bincount(inverse, weights=np.asarray(values)) / counts

        known = set(numeric_required) | {legacy.TIME_COLUMN, legacy.ELAPSED_COLUMN}
        ignored: list[str] = []
        for name in fields:
            if name in known:
                continue
            try:
                values = np.array([float((row.get(name) or "").strip()) for row in rows], dtype=float)
                if not np.isfinite(values).all():
                    raise ValueError
            except ValueError:
                ignored.append(name)
                continue
            channels[name] = np.bincount(inverse, weights=values) / counts

        right_raw = channels[legacy.RIGHT_COLUMN]
        left_raw = channels[left_column]
        pitch_raw = channels[legacy.PITCH_COLUMN]
        gyro = np.column_stack([channels[name] for name in legacy.GYRO_COLUMNS])
        accel = np.column_stack([channels[name] for name in legacy.ACCEL_COLUMNS])
        channels["display/right_raw"] = right_raw
        channels["display/left_raw"] = left_raw
        channels["display/right"] = _smooth(right_raw)
        channels["display/left"] = _smooth(left_raw)
        channels["display/pitch_raw"] = pitch_raw
        channels["display/pitch"] = uniform_filter1d(pitch_raw, min(25, len(seconds)), mode="nearest")
        channels["display/gyro_y_raw"] = gyro[:, 1]
        channels["display/gyro_y"] = uniform_filter1d(gyro[:, 1], min(25, len(seconds)), mode="nearest")
        gyro_slow = uniform_filter1d(gyro, min(101, len(seconds)), axis=0, mode="nearest")
        channels["display/motion"] = np.linalg.norm(gyro - gyro_slow, axis=1)
        accel_mag = np.linalg.norm(accel, axis=1)
        baseline = uniform_filter1d(accel_mag, min(101, len(seconds)), mode="nearest")
        channels["display/impact"] = uniform_filter1d(
            np.abs(accel_mag - baseline), min(51, len(seconds)), mode="nearest"
        )

        diffs = np.diff(seconds)
        gaps = [
            DataGap(int(index), int(index + 1), float(seconds[index]), float(seconds[index + 1]))
            for index in np.flatnonzero(diffs > legacy.DATA_GAP_S)
        ]
        duplicate_rows = len(rows) - len(seconds)
        warnings = []
        if duplicate_rows:
            warnings.append(f"合并 {duplicate_rows} 行重复时间戳")
        if len(rows) > 300_000 or path.stat().st_size > 30 * 1024 * 1024:
            warnings.append("文件超过 30 万行或 30 MB，不受性能验收保证")
        accel_norm = np.linalg.norm(accel, axis=1)
        median_accel = float(np.median(accel_norm))
        if not 0.4 <= median_accel <= 2.0:
            warnings.append(f"加速度模中位数 {median_accel:.3f} 超出协议常见范围，需核对单位或佩戴")
        if ignored:
            warnings.append(f"忽略 {len(ignored)} 个非数值额外列")

        return cls(
            path=path,
            session_id=(session_id or match.group(1)).strip().upper(),
            trial_id=(trial_id or match.group(2)).strip().upper(),
            time_column=time_column,
            seconds=seconds,
            timestamps=timestamps,
            start_datetime=start_datetime,
            channels=channels,
            ignored_columns=tuple(ignored),
            duplicate_rows=duplicate_rows,
            gaps=gaps,
            warnings=warnings,
            source_hash=sha256_file(path),
        )

    @property
    def duration(self) -> float:
        return float(self.seconds[-1])

    def nearest_index(self, seconds: float) -> int:
        pos = int(np.searchsorted(self.seconds, seconds))
        candidates = [max(0, pos - 1), min(len(self.seconds) - 1, pos)]
        return min(candidates, key=lambda index: abs(float(self.seconds[index]) - seconds))

    def locate_timestamp(self, value: str) -> tuple[int, bool]:
        try:
            if self.start_datetime is None:
                seconds = float(value)
            else:
                seconds = (datetime.strptime(value.strip(), legacy.TIME_FORMAT) - self.start_datetime).total_seconds()
        except ValueError as exc:
            raise TrialValidationError([f"标注时间无效：{value!r}"]) from exc
        index = self.nearest_index(seconds)
        return index, abs(float(self.seconds[index]) - seconds) <= 1e-9

    def timestamp(self, index: int) -> str:
        return self.timestamps[int(index)]

    @property
    def file_info(self) -> TrialFileInfo:
        return identify_trial_file(self.path.name)

    @property
    def annotation_mode(self) -> str:
        return self.file_info.mode

    @property
    def is_v3(self) -> bool:
        return self.file_info.is_v3

    @property
    def is_read_only(self) -> bool:
        return self.file_info.is_read_only

    @property
    def is_editable(self) -> bool:
        return self.file_info.editable

    @property
    def is_formal_v3(self) -> bool:
        return self.file_info.is_v3 and self.file_info.is_formal

    @property
    def is_t04_merged(self) -> bool:
        return self.file_info.is_merged

    @property
    def stitch_gap(self) -> DataGap | None:
        if not self.is_t04_merged:
            return None
        candidates = [gap for gap in self.gaps if abs(gap.duration - 3.0) <= 0.001]
        return candidates[0] if len(candidates) == 1 else None

    @property
    def has_legal_stitch(self) -> bool:
        return self.gap_contract().valid and self.stitch_gap is not None

    @property
    def stitch_sample_index(self) -> int | None:
        return self.stitch_gap.after_index if self.has_legal_stitch and self.stitch_gap else None

    def gap_contract(self) -> GapContract:
        gaps = tuple(self.gaps)
        info = self.file_info
        errors: list[str] = []
        stitch_gap = None
        data_gaps = gaps
        if info.is_v3:
            if info.is_merged:
                candidates = [gap for gap in gaps if abs(gap.duration - 3.0) <= 0.001]
                if len(candidates) > 1:
                    errors.append("T04 合并文件存在多个约 3 秒接缝断档")
                elif not candidates:
                    if len(gaps) == 1:
                        errors.append("T04 接缝断档必须为 3.000 s ± 0.001 s")
                    else:
                        errors.append("T04 合并文件缺少唯一约 3 秒接缝断档")
                else:
                    stitch_gap = candidates[0]
                    data_gaps = tuple(gap for gap in gaps if gap != stitch_gap)
            elif gaps:
                data_gaps = gaps
        return GapContract(data_gaps, stitch_gap, tuple(errors))

    def time_display(self, index: int) -> dict[str, float | str | None]:
        index = int(index)
        merged = float(self.seconds[index])
        result: dict[str, float | str | None] = {"merged_seconds": merged}
        if not self.is_t04_merged or self.stitch_gap is None:
            result["source_seconds"] = merged
            return result
        gap = self.stitch_gap
        if merged <= gap.start:
            result.update({"source_video": "1", "source1_seconds": merged, "source2_seconds": None})
        elif merged >= gap.end:
            result.update({"source_video": "2", "source1_seconds": None, "source2_seconds": merged - gap.end})
        else:
            result.update({"source_video": None, "source1_seconds": None, "source2_seconds": None})
        return result

    video_time = time_display

    def inside_gap(self, seconds: float) -> bool:
        return any(gap.start < seconds < gap.end for gap in self.gaps)

    def values_at(self, index: int) -> dict[str, float]:
        names = ("display/left", "display/right", "display/pitch", "display/motion", "display/impact")
        return {name.removeprefix("display/"): float(self.channels[name][index]) for name in names}
