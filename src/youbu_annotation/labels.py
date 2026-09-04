"""Project-level activity and terrain label catalog."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .constants import ACTIVITY_NAMES, V2_TERRAIN_NAMES

_BUILTIN_TERRAIN_NAMES = {name: label for name, label in V2_TERRAIN_NAMES.items() if name != "INCLINE"}


class LabelCatalogError(RuntimeError):
    pass


def _track_key(track: object) -> str:
    key = getattr(track, "value", track)
    if key not in {"activity", "terrain"}:
        raise ValueError(f"未知标签轨道：{track}")
    return str(key)


@dataclass
class LabelCatalog:
    """Enabled/disabled labels shared by annotation files in one directory."""

    activities: dict[str, bool]
    terrains: dict[str, bool]

    SCHEMA = "youbu-annotation-label-catalog"
    SCHEMA_VERSION = 1

    @classmethod
    def default(cls) -> "LabelCatalog":
        return cls(
            {name: True for name in ACTIVITY_NAMES},
            {name: True for name in _BUILTIN_TERRAIN_NAMES},
        )

    @classmethod
    def load(cls, path: Path) -> "LabelCatalog":
        if not path.exists():
            return cls.default()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("schema") != cls.SCHEMA or payload.get("schema_version") != cls.SCHEMA_VERSION:
                raise ValueError("schema 不匹配")
            activities = cls._read_track(payload.get("activities"))
            terrains = cls._read_track(payload.get("terrains"))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, AttributeError) as exc:
            raise LabelCatalogError(f"标签目录无法解析：{path}") from exc
        catalog = cls(activities, terrains)
        catalog._restore_builtins()
        return catalog

    @staticmethod
    def _read_track(value: object) -> dict[str, bool]:
        if not isinstance(value, dict):
            raise ValueError("标签轨道必须是对象")
        result: dict[str, bool] = {}
        for name, state in value.items():
            if not isinstance(name, str):
                raise ValueError("标签名称必须是字符串")
            if isinstance(state, dict):
                state = state.get("enabled", True)
            if not isinstance(state, bool):
                raise ValueError("标签启用状态必须是布尔值")
            result[name] = state
        return result

    def _restore_builtins(self) -> None:
        for name in ACTIVITY_NAMES:
            self.activities[name] = True
        for name in _BUILTIN_TERRAIN_NAMES:
            self.terrains[name] = True

    def save(self, path: Path) -> None:
        path = path.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": self.SCHEMA,
            "schema_version": self.SCHEMA_VERSION,
            "activities": self.activities,
            "terrains": self.terrains,
        }
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _mapping(self, track: object) -> dict[str, bool]:
        return self.activities if _track_key(track) == "activity" else self.terrains

    @staticmethod
    def validate_name(name: str) -> str:
        value = name.strip()
        if not value:
            raise ValueError("标签名称不能为空")
        if len(value) > 100:
            raise ValueError("标签名称不能超过 100 个字符")
        if any(ord(char) < 32 for char in value):
            raise ValueError("标签名称不能包含控制字符")
        return value

    def contains(self, track: object, name: str) -> bool:
        return name in self._mapping(track)

    def is_enabled(self, track: object, name: str) -> bool:
        return self._mapping(track).get(name, False)

    def display(self, track: object, name: str) -> str:
        key = _track_key(track)
        defaults = ACTIVITY_NAMES if key == "activity" else _BUILTIN_TERRAIN_NAMES
        return defaults.get(name, name)

    def entries(
        self,
        track: object,
        *,
        include_disabled: bool = False,
        include: tuple[str, ...] = (),
    ) -> list[tuple[str, str, bool]]:
        mapping = self._mapping(track)
        included = set(include)
        result = []
        for name, enabled in mapping.items():
            if enabled or include_disabled or name in included:
                result.append((name, self.display(track, name), enabled))
        for name in included:
            if name not in mapping:
                result.append((name, self.display(track, name), False))
        return result

    def add(self, track: object, name: str) -> bool:
        value = self.validate_name(name)
        mapping = self._mapping(track)
        if any(existing.casefold() == value.casefold() for existing in mapping):
            raise ValueError(f"标签已存在：{value}")
        mapping[value] = True
        return True

    def disable(self, track: object, name: str) -> None:
        mapping = self._mapping(track)
        if name not in mapping:
            raise ValueError(f"未知标签：{name}")
        key = _track_key(track)
        defaults = ACTIVITY_NAMES if key == "activity" else _BUILTIN_TERRAIN_NAMES
        if name in defaults:
            raise ValueError("内置标签不能停用")
        mapping[name] = False

    def enable(self, track: object, name: str) -> None:
        mapping = self._mapping(track)
        if name not in mapping:
            raise ValueError(f"未知标签：{name}")
        mapping[name] = True

    def discover_rows(self, rows: list[dict[str, str]]) -> bool:
        changed = False
        for row in rows:
            for track, field in (("activity", "activity_truth"), ("terrain", "terrain_truth")):
                name = (row.get(field) or "").strip()
                if name and not self.contains(track, name):
                    self.validate_name(name)
                    self._mapping(track)[name] = True
                    changed = True
        return changed
