"""Stable labels and presentation metadata shared by the editor."""

from __future__ import annotations

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
    "SQUAT_DESCENT": "下蹲",
    "SQUAT_HOLD": "蹲姿保持",
    "SQUAT_ASCENT": "蹲起",
    "HIGH_KNEE_ALTERNATING": "交替高抬腿",
    "STANDING_ADJUSTMENT": "站立调整",
    "OTHER": "其他动作",
}
TERRAIN_NAMES = {"LEVEL": "平地", "ASCENT": "上楼", "DESCENT": "下楼", "INCLINE": "上坡"}

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
}

LEFT_COLOR = "#D84315"
RIGHT_COLOR = "#1565C0"
PITCH_COLOR = "#616161"
MOTION_COLOR = "#2E7D32"
IMPACT_COLOR = "#C62828"


def valid_state(activity: str, terrain: str) -> bool:
    if activity not in ACTIVITY_NAMES or terrain not in TERRAIN_NAMES:
        return False
    if terrain == "LEVEL":
        return True
    if terrain in {"ASCENT", "DESCENT"}:
        return activity in {"STILL", "WALKING"}
    return activity in {"STILL", "WALKING", "OTHER"}


def state_name(activity: str, terrain: str) -> str:
    return f"{ACTIVITY_NAMES.get(activity, activity)}·{TERRAIN_NAMES.get(terrain, terrain)}"
