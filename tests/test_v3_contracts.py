from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pytest

from youbu_annotation import auto_annotate as legacy
from youbu_annotation.auto_adapter import recognize
from youbu_annotation.label_schema import DEFAULT_V3_SCHEMA, Track
from youbu_annotation.model import AnnotationDocument, ConfirmationKind
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


def test_formal_v3_gap_requires_accepted_qa_and_fixed_reconfirmation() -> None:
    trial = make_trial("P01_S01_T03_v3.csv", [DataGap(1, 2, 1.0, 4.0)])
    trial.trial_id = "T03"
    document = AnnotationDocument.from_rows(trial, [])

    issues = [issue for issue in document.issues if issue.code == "input_gap_contract"]
    assert len(issues) == 1
    assert (issues[0].start, issues[0].end) == (1.0, 4.0)
    marker = next(
        item for item in document.confirmations
        if item.kind is ConfirmationKind.GAP_RECONFIRMATION
    )
    assert marker.sample_index == 2
    assert "断档 1.000000–4.000000 s" in marker.user_note
    assert not document.structural_errors()
    with pytest.raises(ValueError, match="QA"):
        document.attest("annotator-1", "2026-08-31T00:00:00Z")
    with pytest.raises(ValueError, match="确认无需修改"):
        document.resolve_issue(issues[0].id, "fixed", "已检查")

    document.resolve_issue(issues[0].id, "accepted", "确认蓝牙断联，断档内无传感器证据")
    assert "判断依据：确认蓝牙断联，断档内无传感器证据" in marker.user_note
    event = next(item for item in document.composed_events() if item.id == marker.id)
    with pytest.raises(ValueError, match="固定"):
        document.move_event(event, 3)
    with pytest.raises(ValueError, match="固定"):
        document.delete_confirmation(marker.id, "")

    document.set_video_files(["T03_review.mp4"])
    document.attest("annotator-1", "2026-08-31T00:00:00Z")
    assert document.ready_to_save


def test_gap_declaration_round_trips_in_csv_and_report(tmp_path: Path) -> None:
    trial = make_trial("P01_S01_T03_v3.csv", [DataGap(1, 2, 1.0, 4.0)])
    trial.trial_id = "T03"
    document = AnnotationDocument.from_rows(trial, [])
    issue = next(issue for issue in document.issues if issue.code == "input_gap_contract")
    document.resolve_issue(issue.id, "accepted", "确认蓝牙断联，断档内无传感器证据")
    document.set_video_files(["T03_review.mp4"])
    document.attest("annotator-1", "2026-09-07T00:00:00Z")

    target = tmp_path / "state_changes.csv"
    repository = AnnotationRepository(target, trial)
    try:
        repository.save(document)
    finally:
        repository.close()

    with target.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    marker_row = next(row for row in rows if row["timestamp"] == "4.0")
    assert len(rows[0]) == 7
    assert "断档后重新确认" in marker_row["notes"]
    assert "判断依据：确认蓝牙断联，断档内无传感器证据" in marker_row["notes"]

    report = json.loads(repository.report_path.read_text(encoding="utf-8"))
    declaration = report["trials"][trial.path.name]["gap_declarations"][0]
    assert declaration["issue"]["reason"] == "确认蓝牙断联，断档内无传感器证据"
    assert declaration["resolved"]

    reopened_repository = AnnotationRepository(target, trial)
    try:
        reopened = reopened_repository.load_document()
    finally:
        reopened_repository.close()
    assert reopened is not None
    assert len([item for item in reopened.confirmations if item.kind is ConfirmationKind.GAP_RECONFIRMATION]) == 1
    reopened_issue = next(issue for issue in reopened.issues if issue.code == "input_gap_contract")
    assert reopened_issue.resolution == "accepted"
    assert reopened_issue.reason == "确认蓝牙断联，断档内无传感器证据"


def test_formal_v3_multiple_gaps_require_individual_declarations() -> None:
    trial = make_trial(
        "P01_S01_T03_v3.csv",
        [DataGap(0, 1, 0.0, 1.0), DataGap(2, 3, 4.0, 5.0)],
    )
    trial.trial_id = "T03"
    document = AnnotationDocument.from_rows(trial, [])

    issues = [issue for issue in document.issues if issue.code == "input_gap_contract"]
    markers = [
        item for item in document.confirmations
        if item.kind is ConfirmationKind.GAP_RECONFIRMATION
    ]
    assert len(issues) == len(markers) == 2
    assert {item.sample_index for item in markers} == {1, 3}
    document.resolve_issue(issues[0].id, "accepted", "确认第一处为采集异常")
    assert not document.ready_to_save
    document.resolve_issue(issues[1].id, "accepted", "确认第二处为采集异常")
    assert all(issue.resolved for issue in issues)


def test_t04_seam_and_extra_gap_use_separate_contracts() -> None:
    trial = make_trial(
        "P01_S01_T04_v3_1+2.csv",
        [DataGap(1, 2, 1.0, 4.0), DataGap(2, 3, 4.0, 5.0)],
    )
    contract = trial.gap_contract()
    assert contract.stitch_gap == trial.gaps[0]
    assert contract.data_gaps == (trial.gaps[1],)
    assert not contract.errors

    document = AnnotationDocument.from_rows(trial, [])
    assert len([item for item in document.composed_events() if item.kind == "stitch_initial"]) == 1
    issue = next(issue for issue in document.issues if issue.code == "input_gap_contract")
    marker = next(
        item for item in document.confirmations
        if item.kind is ConfirmationKind.GAP_RECONFIRMATION
    )
    assert marker.sample_index == 3
    document.resolve_issue(issue.id, "accepted", "确认接缝之外的断档为采集异常")
    document.set_video_files(["T04_part1.mp4", "T04_part2.mp4"])
    document.attest("annotator-1", "2026-09-07T00:00:00Z")
    assert document.ready_to_save


def test_gap_reuses_existing_boundary_without_duplicate_event() -> None:
    trial = make_trial("P01_S01_T03_v3.csv", [DataGap(1, 2, 1.0, 4.0)])
    trial.trial_id = "T03"
    document = AnnotationDocument.from_rows(
        trial,
        [
            {
                "timestamp": "0.0",
                "activity_truth": "STILL",
                "terrain_truth": "LEVEL",
                "notes": "近似：自动检测的文件开头静止",
            },
            {
                "timestamp": "4.0",
                "activity_truth": "WALKING",
                "terrain_truth": "LEVEL",
                "notes": "近似：自动检测的组合状态边界",
            },
        ],
    )
    assert not [
        item for item in document.confirmations
        if item.kind is ConfirmationKind.GAP_RECONFIRMATION
    ]
    boundary = next(item for item in document.boundaries if item.sample_index == 2)
    event = next(item for item in document.composed_events() if item.sample_index == 2)
    assert event.kind == "boundary"
    assert "断档 1.000000–4.000000 s" in boundary.user_note
    with pytest.raises(ValueError, match="固定"):
        document.move_event(event, 3)

    issue = next(issue for issue in document.issues if issue.code == "input_gap_contract")
    document.resolve_issue(issue.id, "accepted", "确认断档后首个真实采样点状态")
    assert "判断依据：确认断档后首个真实采样点状态" in boundary.user_note


def test_t04_multiple_three_second_gaps_remain_a_structural_error() -> None:
    trial = make_trial(
        "P01_S01_T04_v3_1+2.csv",
        [DataGap(0, 1, 0.0, 3.0), DataGap(2, 3, 4.0, 7.0)],
    )
    contract = trial.gap_contract()
    assert contract.stitch_gap is None
    assert any("多个约 3 秒" in error for error in contract.errors)
    document = AnnotationDocument.from_rows(trial, [])
    assert any("多个约 3 秒" in error for error in document.structural_errors())


def test_auto_recognition_materializes_gap_declaration(monkeypatch) -> None:
    trial = make_trial("P01_S01_T03_v3.csv", [DataGap(1, 2, 1.0, 4.0)])
    trial.trial_id = "T03"
    trial.channels[legacy.LEFT_COLUMN_ALT] = trial.channels["display/left"]
    monkeypatch.setattr(legacy.Signal, "from_normalized", lambda *args, **kwargs: object())
    monkeypatch.setattr(legacy, "annotate_file", lambda *args, **kwargs: ([], {"flags": [], "gaps": []}))
    monkeypatch.setattr(legacy, "qa_check", lambda *args, **kwargs: ["数据断档 1 处，最长 3.0s"])

    document = recognize(trial)

    assert len([issue for issue in document.issues if issue.code == "input_gap_contract"]) == 1
    assert not any(issue.code == "automatic_qa" for issue in document.issues)
    assert len([
        item for item in document.confirmations
        if item.kind is ConfirmationKind.GAP_RECONFIRMATION
    ]) == 1


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

    source = make_trial("P01_S01_T04_v3_1.csv", [DataGap(1, 2, 1.0, 4.0)])
    source_document = AnnotationDocument.from_rows(source, [])
    assert source_document.is_read_only
    assert any(
        item.kind is ConfirmationKind.GAP_RECONFIRMATION
        for item in source_document.confirmations
    )
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
