"""Compatibility of accepted legacy gap QA with per-gap review."""

import pytest

from test_v3_contracts import make_trial
from youbu_annotation.model import AnnotationDocument, ReviewIssue
from youbu_annotation.storage import AnnotationRepository
from youbu_annotation.trial import DataGap


def legacy_draft(*, gaps=None, resolution="accepted", reason="蓝牙断联"):
    trial = make_trial("P01_S01_T03_v3.csv", gaps or [DataGap(1, 2, 1.0, 4.0)])
    trial.trial_id = "T03"
    document = AnnotationDocument.from_rows(trial, [])
    old = ReviewIssue(
        "input_gap_contract", "V3 正式/源文件存在未声明数据断档",
        resolution=resolution, reason=reason,
    )
    document.issues.insert(0, old)
    return trial, document.to_dict(), old


@pytest.mark.parametrize("existing_gap_item", [False, True])
def test_single_gap_migration_survives_save_and_reopen(tmp_path, existing_gap_item):
    trial, draft, old = legacy_draft()
    if not existing_gap_item:
        draft["issues"] = [old.to_dict()]
        draft["confirmations"] = []
    document = AnnotationDocument.from_dict(trial, draft)
    assert len(document.issues) == 1
    issue = document.issues[0]
    assert issue.resolved and issue.reason == old.reason
    assert "判断依据：蓝牙断联" in document.confirmations[0].user_note
    assert document.detail["gap_review_migrations"][0]["source_issue"] == old.to_dict()
    assert not document.ensure_gap_declarations(checkpoint=False)
    assert not document.ready_to_save
    document.set_video_files(["review.mp4"])
    document.attest("reviewer", "2026-09-07T00:00:00Z")
    repository = AnnotationRepository(tmp_path / "state_changes.csv", trial)
    try:
        repository.save(document)
        reopened = repository.load_document()
    finally:
        repository.close()
    assert reopened.ready_to_save
    assert len(reopened.issues) == len(reopened.confirmations) == 1
    assert reopened.issues[0].reason == old.reason
    assert len(reopened.detail["gap_review_migrations"]) == 1
    assert reopened.confirmations[0].user_note.count("判断依据：蓝牙断联") == 1


@pytest.mark.parametrize("resolution,reason", [(None, ""), ("fixed", ""), ("accepted", "  ")])
def test_unaccepted_legacy_review_does_not_close_gap(resolution, reason):
    trial, draft, _ = legacy_draft(resolution=resolution, reason=reason)
    document = AnnotationDocument.from_dict(trial, draft)
    assert not document.issues[-1].resolved
    assert "gap_review_migrations" not in document.detail


def test_multiple_gaps_require_individual_review():
    trial, draft, _ = legacy_draft(gaps=[DataGap(0, 1, 0.0, 1.0), DataGap(2, 3, 4.0, 5.0)])
    document = AnnotationDocument.from_dict(trial, draft)
    assert sum(not issue.resolved for issue in document.issues) == 2
    assert "gap_review_migrations" not in document.detail


def test_existing_interval_conclusion_is_preserved():
    trial, draft, _ = legacy_draft()
    draft["issues"][-1].update(resolution="accepted", reason="逐区间复核理由")
    document = AnnotationDocument.from_dict(trial, draft)
    assert document.issues[-1].reason == "逐区间复核理由"
    assert "gap_review_migrations" not in document.detail


def test_overlong_reason_keeps_gap_unresolved():
    trial, draft, _ = legacy_draft(reason="断" * 500)
    document = AnnotationDocument.from_dict(trial, draft)
    assert not document.issues[-1].resolved
    assert "gap_review_migrations" not in document.detail
