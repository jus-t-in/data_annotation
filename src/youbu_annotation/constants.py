"""Stable labels and presentation metadata shared by the editor."""

from __future__ import annotations

import colorsys
import hashlib

APP_NAME = "步态数据标注器"
VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = 1

OUTPUT_FIELDS = (
    "session_id",
    "trial_id",
    "input_file",
    "timestamp",
    "activity_truth",
    "terrain_truth",
    "notes",
)

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
    "OTHER": "其他动作",
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

TERRAIN_COLORS = {"LEVEL": "#83918B", "ASCENT": "#BD7A3B", "DESCENT": "#527B94"}

LEFT_COLOR = "#D84315"
RIGHT_COLOR = "#1565C0"
PITCH_COLOR = "#616161"
MOTION_COLOR = "#2E7D32"
IMPACT_COLOR = "#C62828"
_COLOR_SCOPE_OFFSETS = {"state": 0.0, "terrain": 0.5, "activity": 0.25}
_RESERVED_COLORS = tuple({*STATE_COLORS.values(), *TERRAIN_COLORS.values()})


def _rgb(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))


def _generated_color(scope: str, *values: str) -> str:
    digest = hashlib.sha256("\0".join((scope, *values)).encode("utf-8")).digest()
    base_hue = int.from_bytes(digest[:2], "big") / 65536.0
    saturation = 0.55 + digest[2] / 255.0 * 0.12
    lightness = 0.42 + digest[3] / 255.0 * 0.08
    for step in range(24):
        hue = (base_hue + _COLOR_SCOPE_OFFSETS[scope] + step / 24.0) % 1.0
        red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
        color = "#{:02X}{:02X}{:02X}".format(round(red * 255), round(green * 255), round(blue * 255))
        rgb = _rgb(color)
        distinct = all(
            sum((left - right) ** 2 for left, right in zip(rgb, _rgb(reserved))) >= 55**2
            for reserved in _RESERVED_COLORS
        )
        if distinct:
            return color
    return color


def label_color(track: str, name: str) -> str:
    if track == "terrain":
        return TERRAIN_COLORS[name] if name in TERRAIN_COLORS else _generated_color("terrain", name)
    if track == "activity":
        return STATE_COLORS.get((name, "LEVEL")) or _generated_color("activity", name)
    raise ValueError(f"未知标签轨道：{track}")


def state_color(activity: str, terrain: str) -> str:
    return STATE_COLORS.get((activity, terrain)) or _generated_color("state", activity, terrain)


def valid_state(activity: str, terrain: str, catalog=None) -> bool:
    if catalog is None:
        if activity not in ACTIVITY_NAMES or terrain not in TERRAIN_NAMES:
            return False
    elif not catalog.contains("activity", activity) or not catalog.contains("terrain", terrain):
        return False
    if terrain in {"ASCENT", "DESCENT"}:
        return activity in {"STILL", "WALKING"}
    return True


def state_name(activity: str, terrain: str, catalog=None) -> str:
    activity_name = catalog.display("activity", activity) if catalog else ACTIVITY_NAMES.get(activity, activity)
    terrain_name = catalog.display("terrain", terrain) if catalog else TERRAIN_NAMES.get(terrain, terrain)
    return f"{activity_name}·{terrain_name}"
