from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from youbu_annotation.label_schema import DEFAULT_V3_SCHEMA, Track
from youbu_annotation.model import AnnotationDocument
from youbu_annotation.storage import AnnotationRepository
from youbu_annotation.trial import DataGap, TrialData, identify_trial_file


def make_trial(name: str, gaps: list[DataGap] | None = None) -> TrialData:
    seconds = np.array([0.0, 1.0, 4.0, 5.0])
    zero = np.zeros_like(seconds)
    return TrialData(
        path=Path(name),
        session_id="P01_S01",
        trial_id="T04",
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
        gaps=gaps or [],
        warnings=[],
        source_hash="v3-fixture",
    )


def test_v3_roles_and_legal_composites() -> None:
    assert identify_trial_file("P01_S01_T04_v3_1+2.csv").is_formal
    assert identify_trial_file("P01_S01_T04_v3_1.csv").is_read_only
    assert identify_trial_file("P01_S01_T04_v3.csv").role == "unsupported"
    assert DEFAULT_V3_SCHEMA.has_label(Track.TERRAIN, "DECLINE")
    assert DEFAULT_V3_SCHEMA.allows("STANDING_ADJUSTMENT", "DECLINE")
    assert not DEFAULT_V3_SCHEMA.allows("BEND", "DECLINE")


def test_t04_seam_is_fixed_and_round_trips_without_new_csv_columns(tmp_path: Path) -> None:
    trial = make_trial(
        "P01_S01_T04_v3_1+2.csv",
        [DataGap(1, 2, 1.0, 4.0)],
    )
    document = AnnotationDocument.from_rows(trial, [])
    seam = next(event for event in document.composed_events() if event.kind == "stitch_initial")
    assert seam.sample_index == 2
    assert trial.time_display(2)["source2_seconds"] == 0.0

    with pytest.raises(ValueError, match="固定"):
        document.move_event(seam, 3)
    with pytest.raises(ValueError):
        document.delete_boundary(seam.component_ids[0], "")

    document.set_video_files(["T04_part1.mp4", "T04_part2.mp4"])
    document.attest("annotator-1", "2026-08-31T00:00:00Z")
    assert document.ready_to_save

    target = tmp_path / "state_changes.csv"
    repository = AnnotationRepository(target, trial)
    try:
        repository.save(document)
    finally:
        repository.close()

    reopened_repository = AnnotationRepository(target, trial)
    try:
        reopened = reopened_repository.load_document()
    finally:
        reopened_repository.close()
    assert reopened is not None
    assert any(event.kind == "stitch_initial" for event in reopened.composed_events())
    assert reopened.video_files == ("T04_part1.mp4", "T04_part2.mp4")
    assert target.read_text(encoding="utf-8-sig").splitlines()[0].split(",") == [
        "session_id",
        "trial_id",
        "input_file",
        "timestamp",
        "activity_truth",
        "terrain_truth",
        "notes",
    ]


def test_bad_t04_seam_is_hard_block_and_source_is_read_only() -> None:
    bad_trial = make_trial(
        "P01_S01_T04_v3_1+2.csv",
        [DataGap(1, 2, 1.0, 3.5)],
    )
    bad_document = AnnotationDocument.from_rows(bad_trial, [])
    assert bad_document.structural_errors()
    with pytest.raises(ValueError, match="结构错误"):
        bad_document.attest("annotator-1", "2026-08-31T00:00:00Z")

    source = make_trial("P01_S01_T04_v3_1.csv")
    source_document = AnnotationDocument.from_rows(source, [])
    assert source_document.is_read_only
    with pytest.raises(ValueError, match="只读"):
        source_document.add_boundary(Track.ACTIVITY, 1, "WALKING")


def test_manual_decline_boundary_round_trips_as_code_and_display(tmp_path: Path) -> None:
    trial = make_trial("P01_S01_T03_v3.csv")
    document = AnnotationDocument.from_rows(trial, [])
    document.add_boundary(Track.TERRAIN, 1, "DECLINE")
    document.set_video_files(["T03_review.mp4"])
    document.attest("annotator-1", "2026-08-31T00:00:00Z")

    target = tmp_path / "state_changes.csv"
    repository = AnnotationRepository(target, trial)
    try:
        repository.save(document)
    finally:
        repository.close()

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    row = next(item for item in rows if item["timestamp"] == "1.0")
    assert row["terrain_truth"] == "DECLINE"
    assert row["activity_truth"] == "STILL"

    reopened_repository = AnnotationRepository(target, trial)
    try:
        reopened = reopened_repository.load_document()
    finally:
        reopened_repository.close()
    assert reopened is not None
    assert any(item.value == "DECLINE" for item in reopened.track_boundaries(Track.TERRAIN))
    assert reopened.label_schema.state_name("STILL", "DECLINE") == "静止·下坡"
