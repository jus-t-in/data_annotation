"""Adapter from the legacy offline recognizer to the editable document model."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from . import auto_annotate as legacy

from .label_schema import DEFAULT_V2_SCHEMA, DEFAULT_V3_SCHEMA
from .model import AnnotationDocument, ReviewIssue, Severity, Track
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
    schema = DEFAULT_V3_SCHEMA if trial.is_v3 else DEFAULT_V2_SCHEMA
    legacy_qa_only = trial.is_v3 and trial.trial_id in {"T04", "T05"}
    issues = [
        ReviewIssue(
            code="automatic_qa",
            message=flag.removeprefix("提示："),
            severity=Severity.WARNING if flag.startswith("提示：") else Severity.BLOCKING,
        )
        for flag in flags
        if not legacy_qa_only
    ]
    issues.extend(
        ReviewIssue("input_warning", warning, Severity.WARNING)
        for warning in trial.warnings
    )
    gap_contract = trial.gap_contract()
    if gap_contract.errors:
        issues.append(
            ReviewIssue(
                "input_gap_contract",
                "；".join(gap_contract.errors),
                Severity.BLOCKING,
            )
        )
    suggestion = (
        {
            "source": "v2",
            "label": "V2 建议 / V3 待复核",
            "source_schema": "youbu-v2",
            "target_schema": "youbu-v3",
        }
        if trial.is_v3
        else None
    )
    document = AnnotationDocument.from_rows(
        trial,
        [asdict(event) for event in events],
        issues,
        label_schema=schema,
        detail={
            "recognizer": {
                "module": Path(legacy.__file__).name,
                "sha256": sha256_file(Path(legacy.__file__)),
            },
            "calibration": detail.get("calibration", {}),
            "flags": flags,
            "legacy_qa": flags if trial.is_v3 else [],
            "gaps": detail.get("gaps", []),
            "gap_contract": gap_contract.to_dict(),
            "file_role": trial.file_info.to_dict(),
            "suggestion": suggestion,
            "audit": {"video_files": []},
            "batch_note": (
                "曲面斜坡桥；局部坡面约 10–15°；精确角度和长度不作为标签维度"
                if trial.is_v3 and trial.trial_id in {"T04", "T05"}
                else ""
            ),
        },
    )
    document.ensure_stitch_initial_event(checkpoint=False)
    if trial.is_v3 and trial.trial_id in {"T04", "T05"}:
        terrain_intervals = []
        terrain_boundaries = sorted(
            document.track_boundaries(Track.TERRAIN),
            key=lambda item: item.sample_index,
        )
        for position, boundary in enumerate(terrain_boundaries):
            if boundary.provenance.value != "auto":
                continue
            start = boundary.sample_index
            end = (
                terrain_boundaries[position + 1].sample_index
                if position + 1 < len(terrain_boundaries)
                else len(trial.seconds) - 1
            )
            activity = document.state_at(start)[0]
            terrain = boundary.value
            start_seconds = float(trial.seconds[start])
            end_seconds = float(trial.seconds[end])
            terrain_intervals.append(
                {
                    "start": start_seconds,
                    "end": end_seconds,
                    "activity": activity,
                    "terrain": terrain,
                }
            )
            issues_message = (
                f"V3 地形区间待复核（V2 建议）：{schema.state_name(activity, terrain)} "
                f"{start_seconds:.3f}–{end_seconds:.3f}s"
            )
            document.issues.append(
                ReviewIssue(
                    "v3_terrain_interval",
                    issues_message,
                    Severity.BLOCKING,
                    start=start_seconds,
                    end=end_seconds,
                )
            )
        document.detail["terrain_intervals"] = terrain_intervals
    for component in [*document.boundaries, *document.confirmations]:
        component.evidence = {
            "sample_index": component.sample_index,
            "signals": trial.values_at(component.sample_index),
            "recognizer_note": (component.original or {}).get("notes", ""),
        }
    document.baseline = {}
    document.dirty = True
    return document
