"""Versioned activity/terrain labels and legal composite states."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .constants import (
    ACTIVITY_NAMES,
    STATE_COLORS,
    TERRAIN_NAMES,
    V2_TERRAIN_NAMES,
    V3_TERRAIN_NAMES,
    label_color,
    state_color,
    valid_state,
    valid_v3_state,
)

DEFAULT_COLOR = "#607D8B"


class Track(str, Enum):
    ACTIVITY = "activity"
    TERRAIN = "terrain"


def _validate_text(value: str, field_name: str, limit: int) -> None:
    if not value.strip():
        raise ValueError(f"{field_name}不能为空")
    if len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"{field_name}必须为不超过 {limit} 字的单行文本")


def _validate_color(value: str) -> None:
    if len(value) != 7 or value[0] != "#" or any(char not in "0123456789abcdefABCDEF" for char in value[1:]):
        raise ValueError("颜色必须使用 #RRGGBB 格式")


@dataclass(frozen=True)
class LabelDefinition:
    code: str
    display_name: str
    color: str = DEFAULT_COLOR
    description: str = ""
    active: bool = True

    def __post_init__(self) -> None:
        _validate_text(self.code, "标签 code", 100)
        _validate_text(self.display_name, "标签显示名", 100)
        _validate_color(self.color)
        if len(self.description) > 500 or any(ord(char) < 32 and char != "\n" for char in self.description):
            raise ValueError("标签说明不能超过 500 字或包含非法控制字符")

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "display_name": self.display_name,
            "color": self.color,
            "description": self.description,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, value: dict) -> "LabelDefinition":
        return cls(
            code=value["code"],
            display_name=value["display_name"],
            color=value.get("color", DEFAULT_COLOR),
            description=value.get("description", ""),
            active=bool(value.get("active", True)),
        )


@dataclass(frozen=True)
class StateDefinition:
    activity: str
    terrain: str
    color: str = DEFAULT_COLOR

    def __post_init__(self) -> None:
        _validate_color(self.color)

    def to_dict(self) -> dict:
        return {"activity": self.activity, "terrain": self.terrain, "color": self.color}

    @classmethod
    def from_dict(cls, value: dict) -> "StateDefinition":
        return cls(value["activity"], value["terrain"], value.get("color", DEFAULT_COLOR))


@dataclass(frozen=True)
class LabelSchema:
    schema_id: str
    version: int
    activity_labels: tuple[LabelDefinition, ...]
    terrain_labels: tuple[LabelDefinition, ...]
    states: tuple[StateDefinition, ...]

    def __post_init__(self) -> None:
        _validate_text(self.schema_id, "标签模式 ID", 100)
        if self.version < 1:
            raise ValueError("标签模式版本必须大于等于 1")
        for track in Track:
            labels = self.labels(track, include_inactive=True)
            codes = [item.code for item in labels]
            if not labels:
                raise ValueError(f"{track.value} 轨至少需要一个标签")
            if len(codes) != len(set(codes)):
                raise ValueError(f"{track.value} 轨包含重复标签 code")
        combinations = [(item.activity, item.terrain) for item in self.states]
        if not combinations:
            raise ValueError("标签模式至少需要一个合法组合")
        if len(combinations) != len(set(combinations)):
            raise ValueError("标签模式包含重复合法组合")
        for activity, terrain in combinations:
            if not self.has_label(Track.ACTIVITY, activity) or not self.has_label(Track.TERRAIN, terrain):
                raise ValueError(f"合法组合引用未知标签：{activity} · {terrain}")
        if not any(
            self.has_label(Track.ACTIVITY, item.activity, active_only=True)
            and self.has_label(Track.TERRAIN, item.terrain, active_only=True)
            for item in self.states
        ):
            raise ValueError("标签模式至少需要一个由启用标签构成的合法组合")

    def labels(self, track: Track, *, include_inactive: bool = False) -> tuple[LabelDefinition, ...]:
        labels = self.activity_labels if track is Track.ACTIVITY else self.terrain_labels
        return labels if include_inactive else tuple(item for item in labels if item.active)

    def has_label(self, track: Track, code: str, *, active_only: bool = False) -> bool:
        return any(
            item.code == code and (item.active or not active_only)
            for item in self.labels(track, include_inactive=True)
        )

    def allows(self, activity: str, terrain: str) -> bool:
        return any(item.activity == activity and item.terrain == terrain for item in self.states)

    def state_name(self, activity: str, terrain: str) -> str:
        activity_name = self._label_name(Track.ACTIVITY, activity)
        terrain_name = self._label_name(Track.TERRAIN, terrain)
        return f"{activity_name}·{terrain_name}"

    def state_color(self, activity: str, terrain: str) -> str:
        state = next(
            (item for item in self.states if item.activity == activity and item.terrain == terrain),
            None,
        )
        return state.color if state else DEFAULT_COLOR

    def default_state(self) -> tuple[str, str]:
        state = next(
            item
            for item in self.states
            if self.has_label(Track.ACTIVITY, item.activity, active_only=True)
            and self.has_label(Track.TERRAIN, item.terrain, active_only=True)
        )
        return state.activity, state.terrain

    def _label_name(self, track: Track, code: str) -> str:
        label = next(
            (item for item in self.labels(track, include_inactive=True) if item.code == code),
            None,
        )
        return label.display_name if label else code

    def to_dict(self) -> dict:
        return {
            "schema_id": self.schema_id,
            "version": self.version,
            "activity_labels": [item.to_dict() for item in self.activity_labels],
            "terrain_labels": [item.to_dict() for item in self.terrain_labels],
            "states": [item.to_dict() for item in self.states],
        }

    def content_dict(self) -> dict:
        return {
            "activity_labels": [item.to_dict() for item in self.activity_labels],
            "terrain_labels": [item.to_dict() for item in self.terrain_labels],
            "states": [item.to_dict() for item in self.states],
        }

    @classmethod
    def from_dict(cls, value: dict) -> "LabelSchema":
        return cls(
            schema_id=value["schema_id"],
            version=int(value["version"]),
            activity_labels=tuple(LabelDefinition.from_dict(item) for item in value["activity_labels"]),
            terrain_labels=tuple(LabelDefinition.from_dict(item) for item in value["terrain_labels"]),
            states=tuple(StateDefinition.from_dict(item) for item in value["states"]),
        )

    @property
    def content_hash(self) -> str:
        payload = json.dumps(self.content_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def _label_definitions(
    names: dict[str, str],
    colors: dict[str, str],
) -> tuple[LabelDefinition, ...]:
    return tuple(LabelDefinition(code, name, colors.get(code, DEFAULT_COLOR)) for code, name in names.items())


def create_label_schema(
    *,
    schema_id: str,
    version: int,
    activity_labels: Iterable[LabelDefinition],
    terrain_labels: Iterable[LabelDefinition],
    states: Iterable[StateDefinition],
) -> LabelSchema:
    return LabelSchema(
        schema_id,
        version,
        tuple(activity_labels),
        tuple(terrain_labels),
        tuple(states),
    )


def _default_v2_schema() -> LabelSchema:
    activity_colors = {
        code: STATE_COLORS.get((code, "LEVEL"), DEFAULT_COLOR)
        for code in ACTIVITY_NAMES
    }
    terrain_colors = {
        code: label_color("terrain", code)
        for code in V2_TERRAIN_NAMES
    }
    states = tuple(
        StateDefinition(activity, terrain, STATE_COLORS.get((activity, terrain), DEFAULT_COLOR))
        for activity in ACTIVITY_NAMES
        for terrain in V2_TERRAIN_NAMES
        if valid_state(activity, terrain)
    )
    return LabelSchema(
        schema_id="youbu-v2",
        version=1,
        activity_labels=_label_definitions(ACTIVITY_NAMES, activity_colors),
        terrain_labels=_label_definitions(V2_TERRAIN_NAMES, terrain_colors),
        states=states,
    )


DEFAULT_V2_SCHEMA = _default_v2_schema()


def _default_v3_schema() -> LabelSchema:
    activity_colors = {
        code: label_color("activity", code)
        for code in ACTIVITY_NAMES
    }
    terrain_colors = {
        code: label_color("terrain", code)
        for code in V3_TERRAIN_NAMES
    }
    states = tuple(
        StateDefinition(activity, terrain, state_color(activity, terrain))
        for activity in ACTIVITY_NAMES
        for terrain in V3_TERRAIN_NAMES
        if valid_v3_state(activity, terrain)
    )
    return LabelSchema(
        schema_id="youbu-v3",
        version=1,
        activity_labels=_label_definitions(ACTIVITY_NAMES, activity_colors),
        terrain_labels=_label_definitions(V3_TERRAIN_NAMES, terrain_colors),
        states=states,
    )


DEFAULT_V3_SCHEMA = _default_v3_schema()
