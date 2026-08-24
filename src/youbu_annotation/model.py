"""Editable activity/terrain boundary model and domain validation."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from .constants import valid_state
from .label_schema import DEFAULT_V2_SCHEMA, LabelSchema, Track
from .labels import LabelCatalog
from .trial import TrialData

EVENT_NAMESPACE = uuid.UUID("882cd0e8-4f5e-4c03-a744-bc31f6ee6a09")


class Provenance(str, Enum):
    AUTO = "auto"
    ADJUSTED = "adjusted"
    MANUAL = "manual"
    IMPORTED_UNKNOWN = "imported_unknown"


class ConfirmationKind(str, Enum):
    STAIR_SECOND_STEP = "stair_second_step"
    TRIAL_END = "trial_end"


class Severity(str, Enum):
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass
class Boundary:
    track: Track
    sample_index: int
    value: str
    provenance: Provenance
    user_note: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original: dict | None = None
    evidence: dict = field(default_factory=dict)
    parent_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = copy.deepcopy(vars(self))
        result["track"] = self.track.value
        result["provenance"] = self.provenance.value
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "Boundary":
        data = copy.deepcopy(value)
        data["track"] = Track(data["track"])
        data["provenance"] = Provenance(data["provenance"])
        return cls(**data)


@dataclass
class Confirmation:
    kind: ConfirmationKind
    sample_index: int
    provenance: Provenance
    user_note: str = ""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original: dict | None = None
    evidence: dict = field(default_factory=dict)
    parent_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        result = copy.deepcopy(vars(self))
        result["kind"] = self.kind.value
        result["provenance"] = self.provenance.value
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "Confirmation":
        data = copy.deepcopy(value)
        data["kind"] = ConfirmationKind(data["kind"])
        data["provenance"] = Provenance(data["provenance"])
        return cls(**data)


@dataclass
class ReviewIssue:
    code: str
    message: str
    severity: Severity = Severity.BLOCKING
    start: float | None = None
    end: float | None = None
    resolution: str | None = None
    reason: str = ""
    id: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raw = json.dumps(
                [self.code, self.message, self.start, self.end],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.id = hashlib.sha256(raw.encode()).hexdigest()[:20]

    @property
    def resolved(self) -> bool:
        return self.resolution in {"fixed", "accepted"} and (
            self.resolution != "accepted" or bool(self.reason.strip())
        )

    def to_dict(self) -> dict:
        result = copy.deepcopy(vars(self))
        result["severity"] = self.severity.value
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "ReviewIssue":
        data = copy.deepcopy(value)
        data["severity"] = Severity(data["severity"])
        return cls(**data)


@dataclass(frozen=True)
class ComposedEvent:
    id: str
    sample_index: int
    kind: str
    activity: str
    terrain: str
    provenance: Provenance
    user_note: str
    component_ids: tuple[str, ...]


def stable_component_id(trial: TrialData, sample_index: int, role: str, occurrence: int = 0) -> str:
    key = f"{trial.source_hash}|{trial.path.name}|{sample_index}|{role}|{occurrence}"
    return str(uuid.uuid5(EVENT_NAMESPACE, key))


def provenance_from_note(note: str) -> Provenance:
    if "人工调整" in note or "人工修订" in note:
        return Provenance.ADJUSTED
    if "人工新建" in note:
        return Provenance.MANUAL
    if "自动检测" in note or "自动生成未改" in note:
        return Provenance.AUTO
    return Provenance.IMPORTED_UNKNOWN


def user_note_from_note(note: str) -> str:
    if "；" in note:
        return note.split("；", 1)[1].strip()
    if provenance_from_note(note) is Provenance.IMPORTED_UNKNOWN:
        return note.removeprefix("近似：").strip()
    return ""


def confirmation_kind(note: str, activity: str, terrain: str) -> ConfirmationKind | None:
    if "第二步" in note:
        return ConfirmationKind.STAIR_SECOND_STEP
    if "收尾确认" in note or "结束静止确认" in note:
        return ConfirmationKind.TRIAL_END
    if terrain != "LEVEL":
        return ConfirmationKind.STAIR_SECOND_STEP
    if (activity, terrain) == ("STILL", "LEVEL"):
        return ConfirmationKind.TRIAL_END
    return None


class AnnotationDocument:
    def __init__(
        self,
        trial: TrialData,
        boundaries: Iterable[Boundary],
        confirmations: Iterable[Confirmation] = (),
        issues: Iterable[ReviewIssue] = (),
        *,
        label_schema: LabelSchema | None = None,
        detail: dict | None = None,
        deleted: list[dict] | None = None,
        reviewed: dict | None = None,
        catalog: LabelCatalog | None = None,
    ):
        self.catalog = catalog or LabelCatalog.default()
        self.trial = trial
        self.label_schema = label_schema or DEFAULT_V2_SCHEMA
        self.boundaries = list(boundaries)
        self.confirmations = list(confirmations)
        self.issues = list(issues)
        self.detail = detail or {}
        self.deleted = deleted or []
        self.reviewed = reviewed
        for item in self.boundaries:
            if not self.catalog.contains(item.track.value, item.value):
                self.catalog.add(item.track.value, item.value)
        self.dirty = False
        self._undo: list[dict] = []
        self._redo: list[dict] = []
        self._normalize()
        self.baseline = self._state_dict()

    def _catalog_mode(self) -> bool:
        return self.label_schema.content_hash == DEFAULT_V2_SCHEMA.content_hash

    def _allows_state(self, activity: str, terrain: str) -> bool:
        return valid_state(activity, terrain, self.catalog) if self._catalog_mode() else self.label_schema.allows(activity, terrain)

    def _has_label(self, track: Track, value: str, *, active_only: bool = False) -> bool:
        if self._catalog_mode():
            return self.catalog.is_enabled(track.value, value) if active_only else self.catalog.contains(track.value, value)
        return self.label_schema.has_label(track, value, active_only=active_only)

    @classmethod
    def from_rows(
        cls,
        trial: TrialData,
        rows: list[dict],
        issues: Iterable[ReviewIssue] = (),
        *,
        label_schema: LabelSchema | None = None,
        detail: dict | None = None,
        catalog: LabelCatalog | None = None,
    ) -> "AnnotationDocument":
        label_schema = label_schema or DEFAULT_V2_SCHEMA
        catalog = catalog or LabelCatalog.default()
        catalog.discover_rows(rows)
        if not rows:
            activity, terrain = label_schema.default_state()
            initial = [
                Boundary(Track.ACTIVITY, 0, activity, Provenance.AUTO),
                Boundary(Track.TERRAIN, 0, terrain, Provenance.AUTO),
            ]
            return cls(trial, initial, issues=issues, label_schema=label_schema, detail=detail, catalog=catalog)
        located = []
        imported_issues = list(issues)
        for row in rows:
            index, exact = trial.locate_timestamp(row["timestamp"])
            if not exact:
                imported_issues.append(
                    ReviewIssue(
                        "off_sample_timestamp",
                        f"{row['timestamp']} 不在真实采样点，建议吸附到 {trial.timestamp(index)}",
                    )
                )
            located.append((index, row))
        located.sort(key=lambda item: item[0])

        boundaries: list[Boundary] = []
        confirmations: list[Confirmation] = []
        previous: tuple[str, str] | None = None
        for position, (index, row) in enumerate(located):
            activity = row["activity_truth"].strip()
            terrain = row["terrain_truth"].strip()
            provenance = provenance_from_note(row.get("notes", ""))
            user_note = user_note_from_note(row.get("notes", ""))
            original = {
                "timestamp": row["timestamp"],
                "activity": activity,
                "terrain": terrain,
                "notes": row.get("notes", ""),
            }
            state = (activity, terrain)
            if position == 0:
                boundaries.extend(
                    (
                        Boundary(
                            Track.ACTIVITY,
                            index,
                            activity,
                            provenance,
                            user_note,
                            id=stable_component_id(trial, index, "activity", position),
                            original=original,
                        ),
                        Boundary(
                            Track.TERRAIN,
                            index,
                            terrain,
                            provenance,
                            user_note,
                            id=stable_component_id(trial, index, "terrain", position),
                            original=original,
                        ),
                    )
                )
            elif state == previous:
                kind = confirmation_kind(row.get("notes", ""), activity, terrain)
                if kind is None:
                    imported_issues.append(
                        ReviewIssue("unknown_same_state_event", f"{row['timestamp']} 的同状态事件类型无法识别")
                    )
                else:
                    confirmations.append(
                        Confirmation(
                            kind,
                            index,
                            provenance,
                            user_note,
                            id=stable_component_id(trial, index, kind.value, position),
                            original=original,
                        )
                    )
            else:
                if activity != previous[0]:
                    boundaries.append(
                        Boundary(
                            Track.ACTIVITY,
                            index,
                            activity,
                            provenance,
                            user_note,
                            id=stable_component_id(trial, index, "activity", position),
                            original=original,
                        )
                    )
                if terrain != previous[1]:
                    boundaries.append(
                        Boundary(
                            Track.TERRAIN,
                            index,
                            terrain,
                            provenance,
                            user_note,
                            id=stable_component_id(trial, index, "terrain", position),
                            original=original,
                        )
                    )
            previous = state
        return cls(
            trial,
            boundaries,
            confirmations,
            imported_issues,
            label_schema=label_schema,
            detail=detail,
            catalog=catalog,
        )

    def _state_dict(self) -> dict:
        return {
            "boundaries": [item.to_dict() for item in self.boundaries],
            "confirmations": [item.to_dict() for item in self.confirmations],
            "issues": [item.to_dict() for item in self.issues],
            "deleted": copy.deepcopy(self.deleted),
            "reviewed": copy.deepcopy(self.reviewed),
        }

    def to_dict(self) -> dict:
        result = self._state_dict()
        result["label_schema"] = self.label_schema.to_dict()
        result["detail"] = copy.deepcopy(self.detail)
        result["baseline"] = copy.deepcopy(self.baseline)
        return result

    @classmethod
    def from_dict(
        cls,
        trial: TrialData,
        value: dict,
        *,
        label_schema: LabelSchema | None = None,
        catalog: LabelCatalog | None = None,
    ) -> "AnnotationDocument":
        schema = label_schema or (
            LabelSchema.from_dict(value["label_schema"])
            if "label_schema" in value
            else DEFAULT_V2_SCHEMA
        )
        document = cls(
            trial,
            [Boundary.from_dict(item) for item in value["boundaries"]],
            [Confirmation.from_dict(item) for item in value.get("confirmations", [])],
            [ReviewIssue.from_dict(item) for item in value.get("issues", [])],
            label_schema=schema,
            detail=value.get("detail"),
            deleted=value.get("deleted"),
            reviewed=value.get("reviewed"),
            catalog=catalog,
        )
        document.baseline = copy.deepcopy(value.get("baseline", document._state_dict()))
        document.dirty = document._state_dict() != document.baseline
        return document

    def _restore(self, snapshot: dict) -> None:
        self.boundaries = [Boundary.from_dict(item) for item in snapshot["boundaries"]]
        self.confirmations = [Confirmation.from_dict(item) for item in snapshot["confirmations"]]
        self.issues = [ReviewIssue.from_dict(item) for item in snapshot["issues"]]
        self.deleted = copy.deepcopy(snapshot["deleted"])
        self.reviewed = copy.deepcopy(snapshot["reviewed"])
        self.detail = copy.deepcopy(snapshot.get("detail", self.detail))
        self._normalize()
        self.dirty = self._state_dict() != self.baseline

    def _checkpoint(self) -> None:
        snapshot = self._state_dict()
        snapshot["detail"] = copy.deepcopy(self.detail)
        self._undo.append(snapshot)
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        snapshot = self._state_dict()
        snapshot["detail"] = copy.deepcopy(self.detail)
        self._redo.append(snapshot)
        self._restore(self._undo.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        snapshot = self._state_dict()
        snapshot["detail"] = copy.deepcopy(self.detail)
        self._undo.append(snapshot)
        self._restore(self._redo.pop())
        return True

    def _normalize(self) -> None:
        for track in Track:
            ordered = sorted(self.track_boundaries(track), key=lambda item: item.sample_index)
            kept: list[Boundary] = []
            for item in ordered:
                if (
                    kept
                    and kept[-1].sample_index != item.sample_index
                    and kept[-1].value == item.value
                ):
                    continue
                kept.append(item)
            self.boundaries = [item for item in self.boundaries if item.track != track] + kept
        self.boundaries.sort(key=lambda item: (item.sample_index, item.track.value))
        self.confirmations.sort(key=lambda item: item.sample_index)

    def track_boundaries(self, track: Track) -> list[Boundary]:
        return [item for item in self.boundaries if item.track is track]

    def find_boundary(self, boundary_id: str) -> Boundary:
        return next(item for item in self.boundaries if item.id == boundary_id)

    def state_at(self, sample_index: int) -> tuple[str, str]:
        values = {}
        for track in Track:
            candidates = [item for item in self.track_boundaries(track) if item.sample_index <= sample_index]
            values[track] = max(candidates, key=lambda item: item.sample_index).value if candidates else ""
        return values[Track.ACTIVITY], values[Track.TERRAIN]

    @staticmethod
    def _combined_provenance(items: list[Boundary]) -> Provenance:
        values = {item.provenance for item in items}
        if Provenance.ADJUSTED in values or (Provenance.MANUAL in values and len(values) > 1):
            return Provenance.ADJUSTED
        if values == {Provenance.MANUAL}:
            return Provenance.MANUAL
        if values == {Provenance.AUTO}:
            return Provenance.AUTO
        return Provenance.IMPORTED_UNKNOWN

    @staticmethod
    def _combined_note(items: list[Boundary]) -> str:
        notes = [(item.track, item.user_note.strip()) for item in items if item.user_note.strip()]
        distinct = list(dict.fromkeys(note for _, note in notes))
        if len(distinct) <= 1:
            return distinct[0] if distinct else ""
        labels = {Track.ACTIVITY: "活动", Track.TERRAIN: "地形"}
        return "；".join(f"{labels[track]}：{note}" for track, note in notes)

    def composed_events(self) -> list[ComposedEvent]:
        groups: dict[int, list[Boundary]] = {}
        for item in self.boundaries:
            groups.setdefault(item.sample_index, []).append(item)
        result = []
        for index in sorted(groups):
            items = groups[index]
            component_ids = tuple(sorted(item.id for item in items))
            event_id = component_ids[0] if len(component_ids) == 1 else str(
                uuid.uuid5(EVENT_NAMESPACE, "|".join(component_ids))
            )
            activity, terrain = self.state_at(index)
            result.append(
                ComposedEvent(
                    event_id,
                    index,
                    "initial" if index == 0 else "boundary",
                    activity,
                    terrain,
                    self._combined_provenance(items),
                    self._combined_note(items),
                    component_ids,
                )
            )
        for item in self.confirmations:
            activity, terrain = self.state_at(item.sample_index)
            result.append(
                ComposedEvent(
                    item.id,
                    item.sample_index,
                    item.kind.value,
                    activity,
                    terrain,
                    item.provenance,
                    item.user_note,
                    (item.id,),
                )
            )
        return sorted(result, key=lambda item: (item.sample_index, 0 if item.kind in {"initial", "boundary"} else 1))

    def intervals(self) -> list[tuple[int, int, str, str]]:
        state_events = [item for item in self.composed_events() if item.kind in {"initial", "boundary"}]
        result = []
        for index, event in enumerate(state_events):
            end = state_events[index + 1].sample_index if index + 1 < len(state_events) else len(self.trial.seconds) - 1
            result.append((event.sample_index, end, event.activity, event.terrain))
        return result

    def structural_errors(self) -> list[str]:
        errors = []
        sample_count = len(self.trial.seconds)
        for track in Track:
            items = sorted(self.track_boundaries(track), key=lambda item: item.sample_index)
            if not items or items[0].sample_index != 0:
                errors.append(f"{track.value} 缺少首采样点起始状态")
            if len({item.sample_index for item in items}) != len(items):
                errors.append(f"{track.value} 同一采样点存在冲突边界")
            if any(a.value == b.value for a, b in zip(items, items[1:])):
                errors.append(f"{track.value} 存在相邻同标签边界")
            if any(not 0 <= item.sample_index < sample_count for item in items):
                errors.append(f"{track.value} 存在越界边界")
        for event in self.composed_events():
            if not self._allows_state(event.activity, event.terrain):
                errors.append(f"{self.trial.timestamp(event.sample_index)} 的组合状态非法")
        occupied = {item.sample_index for item in self.boundaries}
        if len({item.sample_index for item in self.confirmations}) != len(self.confirmations):
            errors.append("同一采样点存在多个确认标记")
        if any(item.sample_index in occupied for item in self.confirmations):
            errors.append("确认标记与状态边界共享采样点")
        if any(not 0 <= item.sample_index < sample_count for item in self.confirmations):
            errors.append("存在越界确认标记")
        stair_counts: dict[tuple[int, int], int] = {}
        terrain = sorted(self.track_boundaries(Track.TERRAIN), key=lambda item: item.sample_index)
        for confirmation in self.confirmations:
            activity, terrain_value = self.state_at(confirmation.sample_index)
            if confirmation.kind is ConfirmationKind.STAIR_SECOND_STEP:
                host = None
                for index, boundary in enumerate(terrain):
                    end = terrain[index + 1].sample_index if index + 1 < len(terrain) else len(self.trial.seconds)
                    if boundary.sample_index < confirmation.sample_index < end and boundary.value in {"ASCENT", "DESCENT"}:
                        host = (boundary.sample_index, end)
                        break
                if host is None or activity != "WALKING":
                    errors.append("楼梯第二步确认不在有效楼梯行走区间内")
                else:
                    stair_counts[host] = stair_counts.get(host, 0) + 1
            elif (activity, terrain_value) != ("STILL", "LEVEL"):
                errors.append("试次收尾确认不在静止·平地区间内")
            if len(confirmation.user_note) > 500 or any(ord(char) < 32 for char in confirmation.user_note):
                errors.append("确认标记备注含控制字符或超过 500 字")
        if any(count > 1 for count in stair_counts.values()):
            errors.append("同一楼梯区间存在多个第二步确认")
        if sum(item.kind is ConfirmationKind.TRIAL_END for item in self.confirmations) > 1:
            errors.append("试次存在多个收尾确认")
        for item in self.boundaries:
            if not self._has_label(item.track, item.value):
                errors.append(f"未知标签：{item.value}")
            if len(item.user_note) > 500 or any(ord(char) < 32 for char in item.user_note):
                errors.append("边界备注含控制字符或超过 500 字")
        return list(dict.fromkeys(errors))

    def _changed(self) -> None:
        self._normalize()
        self.reviewed = None
        self.dirty = self._state_dict() != self.baseline

    def move_boundary(self, boundary_id: str, sample_index: int) -> None:
        item = self.find_boundary(boundary_id)
        if item.sample_index == 0:
            raise ValueError("起始状态时刻不可移动")
        peers = sorted(self.track_boundaries(item.track), key=lambda value: value.sample_index)
        position = peers.index(item)
        low = peers[position - 1].sample_index + 1
        high = peers[position + 1].sample_index - 1 if position + 1 < len(peers) else len(self.trial.seconds) - 1
        self._checkpoint()
        item.sample_index = max(low, min(high, int(sample_index)))
        if item.provenance is not Provenance.MANUAL:
            item.provenance = Provenance.ADJUSTED
        self._changed()

    def move_event(self, event: ComposedEvent, sample_index: int) -> None:
        components = [self.find_boundary(item_id) for item_id in event.component_ids if any(
            boundary.id == item_id for boundary in self.boundaries
        )]
        if not components:
            confirmation = next(item for item in self.confirmations if item.id == event.id)
            self._checkpoint()
            confirmation.sample_index = max(0, min(len(self.trial.seconds) - 1, int(sample_index)))
            if confirmation.provenance is not Provenance.MANUAL:
                confirmation.provenance = Provenance.ADJUSTED
            self._changed()
            return
        if any(item.sample_index == 0 for item in components):
            raise ValueError("起始状态时刻不可移动")
        self._checkpoint()
        for item in components:
            peers = sorted(self.track_boundaries(item.track), key=lambda value: value.sample_index)
            position = peers.index(item)
            low = peers[position - 1].sample_index + 1
            high = peers[position + 1].sample_index - 1 if position + 1 < len(peers) else len(self.trial.seconds) - 1
            item.sample_index = max(low, min(high, int(sample_index)))
            if item.provenance is not Provenance.MANUAL:
                item.provenance = Provenance.ADJUSTED
        self._changed()

    def set_boundary_value(self, boundary_id: str, value: str) -> None:
        item = self.find_boundary(boundary_id)
        if not self._has_label(item.track, value, active_only=True):
            raise ValueError(f"未知标签：{value}")
        self._checkpoint()
        item.value = value
        if item.provenance is not Provenance.MANUAL:
            item.provenance = Provenance.ADJUSTED
        self._changed()

    def set_note(self, component_id: str, note: str) -> None:
        if len(note) > 500 or any(ord(char) < 32 for char in note):
            raise ValueError("备注必须为不超过 500 字的单行文本")
        item = next(
            (value for value in [*self.boundaries, *self.confirmations] if value.id == component_id),
            None,
        )
        if item is None:
            raise KeyError(component_id)
        self._checkpoint()
        item.user_note = note
        self.dirty = self._state_dict() != self.baseline

    def add_boundary(self, track: Track, sample_index: int, value: str) -> Boundary:
        if not self._has_label(track, value, active_only=True):
            raise ValueError(f"未知标签：{value}")
        if not 0 <= sample_index < len(self.trial.seconds):
            raise ValueError("边界时刻超出试次范围")
        self._checkpoint()
        item = Boundary(track, int(sample_index), value, Provenance.MANUAL)
        self.boundaries.append(item)
        self._changed()
        return item

    def add_interval(self, track: Track, start: int, end: int, value: str) -> None:
        if not self._has_label(track, value, active_only=True):
            raise ValueError(f"未知标签：{value}")
        if not 0 < start < end < len(self.trial.seconds):
            raise ValueError("区间必须位于试次内部并至少跨越一个采样间隔")
        return_value = self.state_at(end)[0 if track is Track.ACTIVITY else 1]
        self._checkpoint()
        self.boundaries.extend(
            (
                Boundary(track, int(start), value, Provenance.MANUAL),
                Boundary(track, int(end), return_value, Provenance.MANUAL),
            )
        )
        self._changed()

    def delete_boundary(self, boundary_id: str, reason: str) -> None:
        item = self.find_boundary(boundary_id)
        if item.sample_index == 0:
            raise ValueError("起始状态不可删除")
        if item.provenance is Provenance.AUTO and not reason.strip():
            raise ValueError("删除自动边界必须填写理由")
        self._checkpoint()
        self.boundaries.remove(item)
        if item.provenance is Provenance.AUTO:
            self.deleted.append({"component": item.to_dict(), "reason": reason})
        self._changed()

    def add_confirmation(self, kind: ConfirmationKind, sample_index: int) -> Confirmation:
        if not 0 <= sample_index < len(self.trial.seconds):
            raise ValueError("确认标记时刻超出试次范围")
        self._checkpoint()
        item = Confirmation(kind, int(sample_index), Provenance.MANUAL)
        self.confirmations.append(item)
        self._changed()
        return item

    def delete_confirmation(self, confirmation_id: str, reason: str) -> None:
        item = next(value for value in self.confirmations if value.id == confirmation_id)
        if item.provenance is Provenance.AUTO and not reason.strip():
            raise ValueError("删除自动确认标记必须填写理由")
        self._checkpoint()
        self.confirmations.remove(item)
        if item.provenance is Provenance.AUTO:
            self.deleted.append({"component": item.to_dict(), "reason": reason})
        self._changed()

    def resolve_issue(self, issue_id: str, resolution: str, reason: str = "") -> None:
        issue = next(item for item in self.issues if item.id == issue_id)
        if resolution not in {"fixed", "accepted"}:
            raise ValueError("未知 QA 处理结论")
        if resolution == "accepted" and not reason.strip():
            raise ValueError("确认无需修改时必须填写理由")
        self._checkpoint()
        issue.resolution = resolution
        issue.reason = reason.strip()
        self.dirty = self._state_dict() != self.baseline

    def attest(self, annotator_id: str, timestamp: str) -> None:
        if self.structural_errors():
            raise ValueError("结构错误未修复，不能声明已复核")
        if any(not issue.resolved for issue in self.issues):
            raise ValueError("QA 复核项未闭环，不能声明已复核")
        if not annotator_id.strip():
            raise ValueError("必须填写标注员 ID")
        if not timestamp.strip():
            raise ValueError("复核时间不能为空")
        self._checkpoint()
        self.reviewed = {"annotator_id": annotator_id.strip(), "timestamp": timestamp}
        self.dirty = self._state_dict() != self.baseline

    def clear_attestation(self) -> None:
        if self.reviewed is None:
            return
        self._checkpoint()
        self.reviewed = None
        self.dirty = self._state_dict() != self.baseline

    def replace_with(self, generated: "AnnotationDocument") -> None:
        """Replace the current draft as one undoable recognition action."""
        if generated.trial.source_hash != self.trial.source_hash:
            raise ValueError("自动识别结果不属于当前试次")
        if generated.label_schema.content_hash != self.label_schema.content_hash:
            raise ValueError("自动识别结果使用了不同的标签模式")
        self._checkpoint()
        self.boundaries = copy.deepcopy(generated.boundaries)
        self.confirmations = copy.deepcopy(generated.confirmations)
        self.issues = copy.deepcopy(generated.issues)
        self.detail = copy.deepcopy(generated.detail)
        self.deleted = copy.deepcopy(generated.deleted)
        self.reviewed = None
        self._changed()

    def mark_saved(self) -> None:
        self.baseline = self._state_dict()
        self.dirty = False

    @property
    def ready_to_save(self) -> bool:
        return not self.structural_errors() and all(issue.resolved for issue in self.issues) and self.reviewed is not None

    def statistics(self) -> dict:
        durations: dict[str, float] = {}
        counts: dict[str, int] = {}
        gap_ranges = [(gap.start, gap.end) for gap in self.trial.gaps]
        for start_index, end_index, activity, terrain in self.intervals():
            start = float(self.trial.seconds[start_index])
            end = float(self.trial.seconds[end_index])
            observed = end - start
            for gap_start, gap_end in gap_ranges:
                observed -= max(0.0, min(end, gap_end) - max(start, gap_start))
            for label in (activity, terrain):
                durations[label] = durations.get(label, 0.0) + max(0.0, observed)
                counts[label] = counts.get(label, 0) + 1
        return {
            "durations_seconds": {key: round(value, 3) for key, value in durations.items()},
            "interval_counts": counts,
            "confirmation_count": len(self.confirmations),
            "unresolved_qa": sum(not item.resolved for item in self.issues),
        }
