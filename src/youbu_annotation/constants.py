"""Stable labels and presentation metadata shared by the editor."""

from __future__ import annotations

import colorsys
import hashlib

APP_NAME = "步态数据标注器"
VERSION = "0.1.0"
REPORT_SCHEMA_VERSION = 2

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
    "SQUAT_DESCENT": "下蹲",
    "SQUAT_HOLD": "蹲姿保持",
    "SQUAT_ASCENT": "蹲起",
    "HIGH_KNEE_ALTERNATING": "交替高抬腿",
    "STANDING_ADJUSTMENT": "站立调整",
    "OTHER": "其他动作",
}
V2_TERRAIN_NAMES = {
    "LEVEL": "平地",
    "ASCENT": "上楼/上坡",
    "DESCENT": "下楼/下坡",
    "INCLINE": "上坡",
}
V3_TERRAIN_NAMES = {
    "LEVEL": "平地",
    "ASCENT": "上楼",
    "DESCENT": "下楼",
    "INCLINE": "上坡",
    "DECLINE": "下坡",
}
# Public names include the V3 label.  V2 callers should use V2_TERRAIN_NAMES
# (or the versioned schema) when enumerating legal labels.
TERRAIN_NAMES = V3_TERRAIN_NAMES

STATE_COLORS = {
    ("STILL", "LEVEL"): "#9E9E9E",
    ("WALKING", "LEVEL"): "#4CAF50",
    ("WALKING", "ASCENT"): "#FF9800",
    ("WALKING", "DESCENT"): "#2196F3",
    ("STILL", "ASCENT"): "#FFC107",
    ("STILL", "DESCENT"): "#03A9F4",
    ("STILL", "INCLINE"): "#6D9E9E",
    ("WALKING", "INCLINE"): "#00897B",
    ("BEND", "LEVEL"): "#8E44AD",
    ("SQUAT_DESCENT", "LEVEL"): "#E91E63",
    ("SQUAT_HOLD", "LEVEL"): "#C2185B",
    ("SQUAT_ASCENT", "LEVEL"): "#AD1457",
    ("HIGH_KNEE_ALTERNATING", "LEVEL"): "#7CB342",
    ("STANDING_ADJUSTMENT", "LEVEL"): "#795548",
    ("OTHER", "LEVEL"): "#F44336",
    ("OTHER", "INCLINE"): "#EF6C00",
    ("STILL", "DECLINE"): "#546E7A",
    ("WALKING", "DECLINE"): "#3949AB",
    ("STANDING_ADJUSTMENT", "ASCENT"): "#8D6E63",
    ("STANDING_ADJUSTMENT", "DESCENT"): "#6D4C41",
    ("STANDING_ADJUSTMENT", "INCLINE"): "#5D8A8A",
    ("STANDING_ADJUSTMENT", "DECLINE"): "#455A64",
    ("OTHER", "DECLINE"): "#C62828",
}

LEFT_COLOR = "#D84315"
RIGHT_COLOR = "#1565C0"
PITCH_COLOR = "#616161"
MOTION_COLOR = "#2E7D32"
IMPACT_COLOR = "#C62828"
TERRAIN_COLORS = {
    "LEVEL": "#83918B",
    "ASCENT": "#BD7A3B",
    "DESCENT": "#527B94",
    "INCLINE": "#4F8A80",
    "DECLINE": "#7B5876",
}
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
        if all(sum((left - right) ** 2 for left, right in zip(_rgb(color), _rgb(reserved))) >= 55**2 for reserved in _RESERVED_COLORS):
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
    if catalog is None and (activity not in ACTIVITY_NAMES or terrain not in V2_TERRAIN_NAMES):
        return False
    if catalog is not None and (not catalog.contains("activity", activity) or not catalog.contains("terrain", terrain)):
        return False
    if terrain == "LEVEL":
        return True
    if terrain in {"ASCENT", "DESCENT"}:
        return activity in {"STILL", "WALKING"}
    return activity in {"STILL", "WALKING", "OTHER"} or catalog is not None


def valid_v3_state(activity: str, terrain: str) -> bool:
    """Return whether a V3 activity/terrain composite is legal."""
    return activity in ACTIVITY_NAMES and terrain in V3_TERRAIN_NAMES and (
        terrain == "LEVEL"
        or activity in {"STILL", "WALKING", "STANDING_ADJUSTMENT", "OTHER"}
    )


def state_name(activity: str, terrain: str, catalog=None) -> str:
    activity_name = catalog.display("activity", activity) if catalog else ACTIVITY_NAMES.get(activity, activity)
    terrain_name = catalog.display("terrain", terrain) if catalog else TERRAIN_NAMES.get(terrain, terrain)
    return f"{activity_name}·{terrain_name}"
