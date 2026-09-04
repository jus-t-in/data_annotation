from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from youbu_annotation.constants import OUTPUT_FIELDS
from youbu_annotation.label_schema import (
    DEFAULT_V2_SCHEMA,
    DEFAULT_V3_SCHEMA,
    LabelDefinition,
    LabelSchema,
    StateDefinition,
    Track,
)
from youbu_annotation.model import AnnotationDocument, Boundary, Provenance, ReviewIssue
from youbu_annotation.storage import AnnotationConflictError, AnnotationRepository
from youbu_annotation.trial import TrialData


def make_trial(path: Path | None = None, *, name: str = "P01_S01_T01_.csv") -> TrialData:
    seconds = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    zero = np.zeros_like(seconds)
    return TrialData(
        path=path or Path(name),
        session_id="P01_S01",
        trial_id="T01",
        time_column="Elapsed (s)",
        seconds=seconds,
        timestamps=[f"{value:.1f}" for value in seconds],
        start_datetime=None,
        channels={
            "display/left": zero,
            "display/right": zero,
            "display/pitch": zero,
            "display/motion": zero,
            "display/impact": zero,
        },
        ignored_columns=(),
        duplicate_rows=0,
        gaps=[],
        warnings=[],
        source_hash="fixture-hash",
    )


def custom_schema(*, custom_active: bool = True) -> LabelSchema:
    return LabelSchema(
        schema_id="lab-schema",
        version=3,
        activity_labels=(
            LabelDefinition("REST", "休息", "#777777"),
            LabelDefinition("CUSTOM", "自定义动作", "#D1495B", "项目自定义", custom_active),
        ),
        terrain_labels=(LabelDefinition("FLOOR", "室内地面", "#3C6E71"),),
        states=(
            StateDefinition("REST", "FLOOR", "#777777"),
            StateDefinition("CUSTOM", "FLOOR", "#D1495B"),
        ),
    )


def test_default_schemas_keep_versioned_terrain_display_names() -> None:
    assert DEFAULT_V2_SCHEMA.has_label(Track.ACTIVITY, "STILL", active_only=True)
    assert DEFAULT_V2_SCHEMA.has_label(Track.TERRAIN, "INCLINE", active_only=True)
    assert DEFAULT_V2_SCHEMA.allows("WALKING", "INCLINE")
    assert not DEFAULT_V2_SCHEMA.allows("BEND", "INCLINE")
    assert DEFAULT_V2_SCHEMA.state_name("WALKING", "ASCENT") == "行走·上楼/上坡"
    assert DEFAULT_V2_SCHEMA.state_name("WALKING", "DESCENT") == "行走·下楼/下坡"
    assert DEFAULT_V2_SCHEMA.state_color("WALKING", "ASCENT") == "#FF9800"
    assert DEFAULT_V3_SCHEMA.state_name("WALKING", "ASCENT") == "行走·上楼"
    assert DEFAULT_V3_SCHEMA.state_name("WALKING", "DESCENT") == "行走·下楼"
    assert DEFAULT_V3_SCHEMA.state_name("WALKING", "INCLINE") == "行走·上坡"
    assert DEFAULT_V3_SCHEMA.state_name("WALKING", "DECLINE") == "行走·下坡"


def test_custom_schema_round_trip_preserves_display_properties_and_hash() -> None:
    schema = custom_schema()
    restored = LabelSchema.from_dict(schema.to_dict())

    assert restored == schema
    assert restored.content_hash == schema.content_hash
    assert restored.state_name("CUSTOM", "FLOOR") == "自定义动作·室内地面"
    assert restored.state_color("CUSTOM", "FLOOR") == "#D1495B"


def test_document_uses_custom_schema_and_keeps_inactive_historical_labels() -> None:
    trial = make_trial()
    document = AnnotationDocument.from_rows(trial, [], label_schema=custom_schema())
    custom = document.add_boundary(Track.ACTIVITY, 2, "CUSTOM")

    restored = AnnotationDocument.from_dict(trial, document.to_dict())
    assert restored.find_boundary(custom.id).value == "CUSTOM"
    assert restored.label_schema.schema_id == "lab-schema"
    assert not restored.structural_errors()

    inactive = AnnotationDocument(
        trial,
        restored.boundaries,
        label_schema=custom_schema(custom_active=False),
    )
    assert not inactive.structural_errors()
    with pytest.raises(ValueError, match="未知标签"):
        inactive.add_boundary(Track.ACTIVITY, 3, "CUSTOM")


def test_document_composes_events_and_undoes_one_domain_command() -> None:
    trial = make_trial()
    document = AnnotationDocument.from_rows(trial, [])
    activity = document.add_boundary(Track.ACTIVITY, 2, "WALKING")
    terrain = document.add_boundary(Track.TERRAIN, 2, "ASCENT")

    event = next(item for item in document.composed_events() if item.sample_index == 2)
    assert (event.activity, event.terrain) == ("WALKING", "ASCENT")
    assert set(event.component_ids) == {activity.id, terrain.id}

    assert document.undo()
    assert document.state_at(2) == ("WALKING", "LEVEL")
    assert document.redo()
    assert document.state_at(2) == ("WALKING", "ASCENT")


def test_moving_composed_event_uses_shared_bounds_and_keeps_components_together() -> None:
    trial = make_trial()
    document = AnnotationDocument(
        trial,
        [
            Boundary(Track.ACTIVITY, 0, "STILL", Provenance.AUTO),
            Boundary(Track.TERRAIN, 0, "LEVEL", Provenance.AUTO),
            Boundary(Track.ACTIVITY, 2, "WALKING", Provenance.AUTO),
            Boundary(Track.TERRAIN, 2, "ASCENT", Provenance.AUTO),
            Boundary(Track.TERRAIN, 3, "LEVEL", Provenance.AUTO),
            Boundary(Track.ACTIVITY, 4, "STILL", Provenance.AUTO),
        ],
    )
    event = next(item for item in document.composed_events() if item.sample_index == 2)

    assert document.event_move_bounds(event) == (1, 2)
    document.move_event(event, 4)
    assert len(document._undo) == 0

    document.move_event(event, 1)
    assert {document.find_boundary(item_id).sample_index for item_id in event.component_ids} == {1}
    assert len(document._undo) == 1
    assert document.undo()
    assert {document.find_boundary(item_id).sample_index for item_id in event.component_ids} == {2}


def test_document_requires_closed_qa_and_attestation_before_save() -> None:
    trial = make_trial()
    issue = ReviewIssue("needs_review", "需要人工复核")
    document = AnnotationDocument.from_rows(trial, [], [issue])

    assert not document.ready_to_save
    document.resolve_issue(issue.id, "accepted", "已检查原始信号")
    assert not document.ready_to_save
    document.attest("annotator-1", "2026-08-17T12:00:00+08:00")
    assert document.ready_to_save


def _row(input_file: str, timestamp: str = "0.0") -> dict[str, str]:
    return {
        "session_id": "P99_S01",
        "trial_id": "T01",
        "input_file": input_file,
        "timestamp": timestamp,
        "activity_truth": "STILL",
        "terrain_truth": "LEVEL",
        "notes": "existing",
    }


def test_csv_repository_preserves_seven_columns_and_other_trials(tmp_path: Path) -> None:
    source = tmp_path / "P01_S01_T01_.csv"
    source.write_text("source\n", encoding="utf-8")
    trial = make_trial(source)
    target = tmp_path / "annotations.csv"
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerow(_row("P99_S01_T01_.csv"))

    repository = AnnotationRepository(target, trial, acquire_lock=False)
    document = AnnotationDocument.from_rows(trial, [])
    document.attest("annotator-1", "2026-08-17T12:00:00+08:00")
    repository.save(document)

    with target.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert tuple(reader.fieldnames or ()) == OUTPUT_FIELDS
    assert [row["input_file"] for row in rows] == ["P99_S01_T01_.csv", source.name]
    assert repository.report_path.exists()


def test_csv_repository_rejects_external_changes(tmp_path: Path) -> None:
    source = tmp_path / "P01_S01_T01_.csv"
    source.write_text("source\n", encoding="utf-8")
    trial = make_trial(source)
    target = tmp_path / "annotations.csv"
    repository = AnnotationRepository(target, trial, acquire_lock=False)
    document = AnnotationDocument.from_rows(trial, [])
    document.attest("annotator-1", "2026-08-17T12:00:00+08:00")
    target.write_text("changed externally\n", encoding="utf-8")

    with pytest.raises(AnnotationConflictError, match="外部修改"):
        repository.save(document)
