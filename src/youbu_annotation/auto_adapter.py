"""Adapter from the legacy offline recognizer to the editable document model."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from . import auto_annotate as legacy

from .model import AnnotationDocument, ReviewIssue, Severity
from .trial import TrialData, sha256_file


def recognize(trial: TrialData) -> AnnotationDocument:
    left_column = next(
        name for name in (legacy.LEFT_COLUMN, legacy.LEFT_COLUMN_ALT) if name in trial.channels
    )
    signal = legacy.Signal.from_normalized(
        trial.path,
        trial.seconds,
        trial.timestamps,
        trial.channels,
        left_column,
    )
    events, detail = legacy.annotate_file(
        trial.path,
        signal,
        session_id=trial.session_id,
        trial_id=trial.trial_id,
    )
    flags = legacy.qa_check(trial.trial_id, events, detail)
    issues = [
        ReviewIssue(
            code="automatic_qa",
            message=flag.removeprefix("提示："),
            severity=Severity.WARNING if flag.startswith("提示：") else Severity.BLOCKING,
        )
        for flag in flags
    ]
    issues.extend(
        ReviewIssue("input_warning", warning, Severity.WARNING)
        for warning in trial.warnings
    )
    document = AnnotationDocument.from_rows(
        trial,
        [asdict(event) for event in events],
        issues,
        detail={
            "recognizer": {
                "module": Path(legacy.__file__).name,
                "sha256": sha256_file(Path(legacy.__file__)),
            },
            "calibration": detail.get("calibration", {}),
            "flags": flags,
            "gaps": detail.get("gaps", []),
        },
    )
    for component in [*document.boundaries, *document.confirmations]:
        component.evidence = {
            "sample_index": component.sample_index,
            "signals": trial.values_at(component.sample_index),
            "recognizer_note": (component.original or {}).get("notes", ""),
        }
    document.baseline = {}
    document.dirty = True
    return document
