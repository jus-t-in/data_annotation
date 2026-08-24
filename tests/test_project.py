from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from youbu_annotation import auto_annotate as legacy
from youbu_annotation.label_schema import LabelDefinition, LabelSchema, StateDefinition, Track
from youbu_annotation.project import (
    AnnotationProject,
    ProjectConflictError,
    ProjectLockError,
    ProjectSourceChangedError,
)


def write_source(path: Path) -> None:
    fields = (
        legacy.ELAPSED_COLUMN,
        legacy.RIGHT_COLUMN,
        legacy.LEFT_COLUMN_ALT,
        legacy.PITCH_COLUMN,
        *legacy.GYRO_COLUMNS,
        *legacy.ACCEL_COLUMNS,
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index in range(5):
            row = {name: "0" for name in fields}
            row[legacy.ELAPSED_COLUMN] = str(index * 0.5)
            row[legacy.RIGHT_COLUMN] = str(index)
            writer.writerow(row)


def add_custom_label(schema: LabelSchema) -> LabelSchema:
    return LabelSchema(
        schema.schema_id,
        schema.version,
        (*schema.activity_labels, LabelDefinition("CUSTOM", "自定义动作", "#D1495B", "项目标签")),
        schema.terrain_labels,
        (*schema.states, StateDefinition("CUSTOM", "LEVEL", "#D1495B")),
    )


def test_project_publishes_new_schema_version_without_mutating_history(tmp_path: Path) -> None:
    project = AnnotationProject.create(tmp_path / "project", name="步态项目")
    try:
        original = project.schema_version(project.schema_draft().schema_id, 1)
        draft = project.schema_draft()
        updated = add_custom_label(draft)

        project.update_schema_draft(updated, expected_hash=draft.content_hash)
        published = project.publish_schema_draft(expected_hash=updated.content_hash)

        assert published.version == 2
        assert published.has_label(Track.ACTIVITY, "CUSTOM", active_only=True)
        assert not original.has_label(Track.ACTIVITY, "CUSTOM")
        assert project.schema_version(published.schema_id, 1) == original
        assert project.schema_draft().version == 3
        with pytest.raises(ProjectConflictError, match="其他页面"):
            project.update_schema_draft(project.schema_draft(), expected_hash=draft.content_hash)
    finally:
        project.close()


def test_project_registers_explicit_identity_for_arbitrary_filename(tmp_path: Path) -> None:
    source = tmp_path / "采集数据.csv"
    write_source(source)
    with AnnotationProject.create(tmp_path / "project", name="步态项目") as project:
        record = project.register_trial(
            source,
            subject_id="subject-7",
            session_id="session-a",
            trial_id="trial-free-name",
        )
        trial = project.load_trial(record.id)

        assert record.input_file == source.name
        assert (record.subject_id, record.session_id, record.trial_id) == (
            "subject-7",
            "SESSION-A",
            "TRIAL-FREE-NAME",
        )
        assert (trial.session_id, trial.trial_id) == (record.session_id, record.trial_id)


def test_project_commits_immutable_revisions_and_rejects_stale_head(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    write_source(source)
    with AnnotationProject.create(tmp_path / "project", name="步态项目") as project:
        record = project.register_trial(source, subject_id="P01", session_id="S01", trial_id="T01")
        _trial, document, head = project.load_document(record.id)
        document.attest("annotator-1", "2026-08-17T12:00:00+08:00")
        first = project.commit_revision(record.id, document, expected_revision_id=head)

        stale = project.revision_document(first.id)
        _trial, current, current_head = project.load_document(record.id)
        boundary = current.add_boundary(Track.ACTIVITY, 2, "WALKING")
        current.attest("annotator-2", "2026-08-17T12:10:00+08:00")
        second = project.commit_revision(record.id, current, expected_revision_id=current_head)

        assert second.parent_id == first.id
        assert not project.load_document(record.id)[1].dirty
        assert project.revision_document(first.id).state_at(2) == ("STILL", "LEVEL")
        assert project.revision_document(second.id).find_boundary(boundary.id).value == "WALKING"
        with pytest.raises(ProjectConflictError, match="重新加载"):
            project.commit_revision(record.id, stale, expected_revision_id=first.id)
        assert [item.id for item in project.revisions(record.id)] == [first.id, second.id]


def test_project_blocks_save_after_source_change_and_uses_online_backup(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    write_source(source)
    project = AnnotationProject.create(tmp_path / "project", name="步态项目")
    try:
        record = project.register_trial(source, subject_id="P01", session_id="S01", trial_id="T01")
        _trial, document, head = project.load_document(record.id)
        document.attest("annotator-1", "2026-08-17T12:00:00+08:00")
        source.write_text(source.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        with pytest.raises(ProjectSourceChangedError, match="内容已变化"):
            project.commit_revision(record.id, document, expected_revision_id=head)

        backup = project.backup(tmp_path / "backup.sqlite3")
        with sqlite3.connect(backup) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        project.close()


def test_project_allows_only_one_open_writer(tmp_path: Path) -> None:
    project = AnnotationProject.create(tmp_path / "project", name="步态项目")
    try:
        with pytest.raises(ProjectLockError, match="其他进程"):
            AnnotationProject.open(project.root)
    finally:
        project.close()

    reopened = AnnotationProject.open(project.root)
    reopened.close()
