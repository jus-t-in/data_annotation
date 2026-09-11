from pathlib import Path

import pytest

from test_trial_contract import write_trial
from youbu_annotation.auto_adapter import recognize
from youbu_annotation.label_schema import Track
from youbu_annotation.model import AnnotationDocument
from youbu_annotation.storage import AnnotationRepository
from youbu_annotation.trial import TrialData, TrialValidationError, identify_trial_file


def test_t06_v3_loads_without_enabling_legacy_or_numbered_parts(tmp_path: Path) -> None:
    path = tmp_path / "P05_S01_T06_v3.csv"
    write_trial(path)
    trial = TrialData.load(path)
    assert (trial.session_id, trial.trial_id) == ("P05_S01", "T06")
    assert trial.file_info.is_formal_v3
    assert trial.file_info.expected_video_count == 1
    assert not identify_trial_file("P05_S01_T06_v3_1.csv").supported
    assert not identify_trial_file("P05_S01_T07_v3.csv").supported
    legacy_path = tmp_path / "P05_S01_T06_v2.csv"
    write_trial(legacy_path)
    with pytest.raises(TrialValidationError, match="文件名"):
        TrialData.load(legacy_path)


def test_t06_suggestions_require_terrain_review(tmp_path: Path) -> None:
    path = tmp_path / "P05_S01_T06_v3.csv"
    write_trial(path)
    document = recognize(TrialData.load(path))
    assert document.is_v3
    assert document.detail["suggestion"]["label"] == "V2 建议 / V3 待复核"
    assert document.detail["batch_note"]
    assert any(issue.code == "v3_terrain_interval" for issue in document.issues)
    assert not any(issue.code == "automatic_qa" for issue in document.issues)
    assert not document.ready_to_save


def test_t06_direct_crest_and_turn_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "P05_S01_T06_v3.csv"
    write_trial(path)
    # Add enough real sample times to represent a crest and a turn separately.
    lines = path.read_text().splitlines()
    lines = [lines[0], lines[1], *[
        str(index) + lines[-1][lines[-1].index(","):]
        for index in range(1, 8)
    ]]
    path.write_text("\n".join(lines) + "\n")
    trial = TrialData.load(path)
    document = AnnotationDocument.from_rows(trial, [])
    document.add_boundary(Track.ACTIVITY, 1, "WALKING")
    document.add_boundary(Track.TERRAIN, 1, "INCLINE")
    document.add_boundary(Track.TERRAIN, 2, "DECLINE")
    document.add_boundary(Track.TERRAIN, 3, "LEVEL")
    document.add_boundary(Track.TERRAIN, 4, "INCLINE")
    document.add_boundary(Track.ACTIVITY, 5, "STANDING_ADJUSTMENT")
    document.add_boundary(Track.ACTIVITY, 6, "WALKING")
    document.add_boundary(Track.TERRAIN, 6, "DECLINE")
    document.add_boundary(Track.ACTIVITY, 7, "STILL")
    document.add_boundary(Track.TERRAIN, 7, "LEVEL")
    document.set_video_files(["P05_S01_T06_v3.mp4"])
    document.attest("test-annotator", "2026-09-07T00:00:00Z")
    target = tmp_path / "state_changes.csv"
    repository = AnnotationRepository(target, trial)
    try:
        repository.save(document)
    finally:
        repository.close()
    repository = AnnotationRepository(target, TrialData.load(path))
    try:
        reopened = repository.load_document()
    finally:
        repository.close()
    assert reopened is not None
    assert reopened.trial.trial_id == "T06"
    assert reopened.state_at(1) == ("WALKING", "INCLINE")
    assert reopened.state_at(2) == ("WALKING", "DECLINE")
    assert reopened.state_at(5) == ("STANDING_ADJUSTMENT", "INCLINE")
    assert reopened.state_at(6) == ("WALKING", "DECLINE")
    assert reopened.video_files == ("P05_S01_T06_v3.mp4",)
