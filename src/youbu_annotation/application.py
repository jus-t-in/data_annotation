"""Application interface shared by interactive adapters."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

from .auto_adapter import recognize
from .label_schema import DEFAULT_V2_SCHEMA, LabelSchema, Track
from .model import AnnotationDocument, ConfirmationKind
from .project import AnnotationProject, ProjectError, TrialRecord
from .trial import TrialData


@dataclass
class Workspace:
    id: str
    trial_record_id: str
    trial: TrialData
    document: AnnotationDocument
    expected_revision_id: str | None


class AnnotationApplication:
    def __init__(self, project: AnnotationProject):
        self.project = project
        self._workspaces: dict[str, Workspace] = {}
        self._lock = threading.RLock()

    def bootstrap(self) -> dict:
        return {
            "project": {
                "id": self.project.project_id,
                "name": self.project.name,
                "root": str(self.project.root),
            },
            "trials": [self._trial_view(item) for item in self.project.trials()],
            "schema_draft": self._schema_view(self.project.schema_draft()),
        }

    def register_trial(
        self,
        source_path: str,
        *,
        subject_id: str,
        session_id: str,
        trial_id: str,
    ) -> dict:
        from pathlib import Path

        with self._lock:
            record = self.project.register_trial(
                Path(source_path),
                subject_id=subject_id,
                session_id=session_id,
                trial_id=trial_id,
            )
            return self._trial_view(record)

    def open_workspace(self, trial_record_id: str, *, recover_draft: bool = True) -> dict:
        with self._lock:
            trial, document, head = self.project.load_document(trial_record_id)
            recovered = False
            if recover_draft:
                draft = self.project.load_draft(trial_record_id)
                if draft is not None and draft[1] == head:
                    document = draft[0]
                    recovered = True
            workspace = Workspace(
                id=str(uuid.uuid4()),
                trial_record_id=trial_record_id,
                trial=trial,
                document=document,
                expected_revision_id=head,
            )
            self._workspaces[workspace.id] = workspace
            result = self.workspace_view(workspace.id)
            result["recovered_draft"] = recovered
            return result

    def close_workspace(self, workspace_id: str) -> None:
        with self._lock:
            if workspace_id not in self._workspaces:
                raise ProjectError("工作区不存在")
            del self._workspaces[workspace_id]

    def workspace_view(self, workspace_id: str) -> dict:
        workspace = self._workspace(workspace_id)
        document = workspace.document
        trial = workspace.trial
        record = self.project.trial(workspace.trial_record_id)
        return {
            "workspace_id": workspace.id,
            "expected_revision_id": workspace.expected_revision_id,
            "trial_record": self._trial_view(record),
            "trial": {
                "sample_count": len(trial.seconds),
                "duration": trial.duration,
                "time_column": trial.time_column,
                "warnings": trial.warnings,
                "gaps": [vars(item) for item in trial.gaps],
            },
            "schema": self._schema_view(document.label_schema),
            "document": {
                "boundaries": [item.to_dict() for item in document.boundaries],
                "confirmations": [item.to_dict() for item in document.confirmations],
                "issues": [item.to_dict() for item in document.issues],
                "events": [self._event_view(trial, item) for item in document.composed_events()],
                "intervals": [
                    {
                        "start_index": start,
                        "end_index": end,
                        "start": float(trial.seconds[start]),
                        "end": float(trial.seconds[end]),
                        "activity": activity,
                        "terrain": terrain,
                        "color": document.label_schema.state_color(activity, terrain),
                        "display_name": document.label_schema.state_name(activity, terrain),
                    }
                    for start, end, activity, terrain in document.intervals()
                ],
                "reviewed": document.reviewed,
                "dirty": document.dirty,
                "ready_to_save": document.ready_to_save,
                "structural_errors": document.structural_errors(),
                "can_undo": bool(document._undo),
                "can_redo": bool(document._redo),
                "statistics": document.statistics(),
            },
            "revisions": [vars(item) for item in self.project.revisions(workspace.trial_record_id)],
        }

    def signals(
        self,
        workspace_id: str,
        *,
        start_index: int = 0,
        end_index: int | None = None,
        max_points: int = 12_000,
    ) -> dict:
        workspace = self._workspace(workspace_id)
        trial = workspace.trial
        size = len(trial.seconds)
        start = max(0, min(size - 1, int(start_index)))
        end = size - 1 if end_index is None else max(start, min(size - 1, int(end_index)))
        count = end - start + 1
        max_points = max(100, min(50_000, int(max_points)))
        if count <= max_points:
            indices = np.arange(start, end + 1, dtype=int)
        else:
            indices = np.unique(np.linspace(start, end, max_points, dtype=int))
        channel_names = ("left", "right", "pitch", "motion", "impact")
        return {
            "indices": indices.tolist(),
            "seconds": trial.seconds[indices].tolist(),
            "channels": {
                name: trial.channels[f"display/{name}"][indices].tolist()
                for name in channel_names
            },
        }

    def command(self, workspace_id: str, action: str, payload: dict | None = None) -> dict:
        payload = payload or {}
        with self._lock:
            workspace = self._workspace(workspace_id)
            document = workspace.document
            if action == "undo":
                document.undo()
            elif action == "redo":
                document.redo()
            elif action == "add_boundary":
                document.add_boundary(
                    Track(payload["track"]),
                    int(payload["sample_index"]),
                    payload["value"],
                )
            elif action == "add_interval":
                document.add_interval(
                    Track(payload["track"]),
                    int(payload["start_index"]),
                    int(payload["end_index"]),
                    payload["value"],
                )
            elif action == "move_event":
                event = next(
                    (item for item in document.composed_events() if item.id == payload["event_id"]),
                    None,
                )
                if event is None:
                    raise ProjectError("标注事件不存在")
                document.move_event(event, int(payload["sample_index"]))
            elif action == "set_boundary_value":
                document.set_boundary_value(payload["boundary_id"], payload["value"])
            elif action == "set_note":
                document.set_note(payload["component_id"], payload.get("note", ""))
            elif action == "delete_component":
                component_id = payload["component_id"]
                if any(item.id == component_id for item in document.boundaries):
                    document.delete_boundary(component_id, payload.get("reason", ""))
                elif any(item.id == component_id for item in document.confirmations):
                    document.delete_confirmation(component_id, payload.get("reason", ""))
                else:
                    raise ProjectError("标注项不存在")
            elif action == "add_confirmation":
                document.add_confirmation(
                    ConfirmationKind(payload["kind"]),
                    int(payload["sample_index"]),
                )
            elif action == "resolve_issue":
                document.resolve_issue(
                    payload["issue_id"],
                    payload["resolution"],
                    payload.get("reason", ""),
                )
            elif action == "attest":
                timestamp = payload.get("timestamp") or datetime.now(timezone.utc).isoformat()
                document.attest(payload["annotator_id"], timestamp)
            elif action == "clear_attestation":
                document.clear_attestation()
            else:
                raise ProjectError(f"未知编辑命令：{action}")
            self.project.save_draft(
                workspace.trial_record_id,
                document,
                expected_revision_id=workspace.expected_revision_id,
            )
            return self.workspace_view(workspace_id)

    def recognize(self, workspace_id: str) -> dict:
        with self._lock:
            workspace = self._workspace(workspace_id)
            if workspace.document.label_schema.content_hash != DEFAULT_V2_SCHEMA.content_hash:
                raise ProjectError("内置识别器只支持默认 V2 标签模式")
            generated = recognize(workspace.trial)
            workspace.document.replace_with(generated)
            self.project.save_draft(
                workspace.trial_record_id,
                workspace.document,
                expected_revision_id=workspace.expected_revision_id,
            )
            return self.workspace_view(workspace_id)

    def commit(self, workspace_id: str) -> dict:
        with self._lock:
            workspace = self._workspace(workspace_id)
            revision = self.project.commit_revision(
                workspace.trial_record_id,
                workspace.document,
                expected_revision_id=workspace.expected_revision_id,
            )
            workspace.expected_revision_id = revision.id
            result = self.workspace_view(workspace_id)
            result["committed_revision"] = vars(revision)
            return result

    def update_schema_draft(self, value: dict, *, expected_hash: str) -> dict:
        with self._lock:
            schema = LabelSchema.from_dict(value)
            return self._schema_view(
                self.project.update_schema_draft(schema, expected_hash=expected_hash)
            )

    def publish_schema_draft(self, *, expected_hash: str) -> dict:
        with self._lock:
            return self._schema_view(
                self.project.publish_schema_draft(expected_hash=expected_hash)
            )

    def _workspace(self, workspace_id: str) -> Workspace:
        try:
            return self._workspaces[workspace_id]
        except KeyError as exc:
            raise ProjectError("工作区不存在") from exc

    @staticmethod
    def _trial_view(record: TrialRecord) -> dict:
        return {
            "id": record.id,
            "subject_id": record.subject_id,
            "session_id": record.session_id,
            "trial_id": record.trial_id,
            "source_path": str(record.source_path),
            "source_hash": record.source_hash,
            "input_file": record.input_file,
            "schema_id": record.schema_id,
            "schema_version": record.schema_version,
        }

    @staticmethod
    def _schema_view(schema: LabelSchema) -> dict:
        result = schema.to_dict()
        result["content_hash"] = schema.content_hash
        return result

    @staticmethod
    def _event_view(trial: TrialData, event) -> dict:
        return {
            "id": event.id,
            "sample_index": event.sample_index,
            "seconds": float(trial.seconds[event.sample_index]),
            "timestamp": trial.timestamp(event.sample_index),
            "kind": event.kind,
            "activity": event.activity,
            "terrain": event.terrain,
            "provenance": event.provenance.value,
            "user_note": event.user_note,
            "component_ids": list(event.component_ids),
        }
