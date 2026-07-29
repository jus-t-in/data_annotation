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

LEFT_COLOR = "#D84315"
RIGHT_COLOR = "#1565C0"
PITCH_COLOR = "#616161"
MOTION_COLOR = "#2E7D32"
IMPACT_COLOR = "#C62828"


def valid_state(activity: str, terrain: str) -> bool:
    if activity not in ACTIVITY_NAMES or terrain not in TERRAIN_NAMES:
        return False
    return terrain == "LEVEL" or activity in {"STILL", "WALKING"}


def state_name(activity: str, terrain: str) -> str:
    return f"{ACTIVITY_NAMES.get(activity, activity)}·{TERRAIN_NAMES.get(terrain, terrain)}"

