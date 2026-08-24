from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from youbu_annotation.auto_annotate import (
    BoutFeatures,
    Event,
    Segment,
    Signal,
    Step,
    classify_bout,
    input_files,
    refine_activity_edges,
    squat_phases,
    stair_pauses,
    treadmill_terrain,
    validate,
    write_events,
)
from youbu_annotation.constants import ACTIVITY_NAMES, STATE_COLORS, valid_state


def event(subject: str, trial: str, timestamp: float, activity: str, terrain: str = "LEVEL") -> Event:
    name = f"{subject}_S01_{trial}_v2.csv"
    return Event(
        f"{subject}_S01",
        trial,
        name,
        f"{timestamp:.2f}",
        activity,
        terrain,
        "test",
    )


def test_v2_labels_and_legal_states_are_complete() -> None:
    assert set(ACTIVITY_NAMES) == {
        "STILL",
        "WALKING",
        "BEND",
        "SQUAT_DESCENT",
        "SQUAT_HOLD",
        "SQUAT_ASCENT",
        "HIGH_KNEE_ALTERNATING",
        "STANDING_ADJUSTMENT",
        "OTHER",
    }
    assert valid_state("WALKING", "INCLINE")
    assert valid_state("OTHER", "INCLINE")
    assert not valid_state("BEND", "INCLINE")
    assert ("WALKING", "INCLINE") in STATE_COLORS


def test_high_knee_requires_sufficient_peak_rate() -> None:
    base = dict(
        duration=8.0,
        right_span=1.5,
        left_span=1.5,
        pitch_ptp=8.0,
        gyro_y_signed_peak=0.0,
        leg_corr=0.0,
        prom_median=1.2,
        interleave=6,
        dominance=0.5,
    )
    assert classify_bout(BoutFeatures(peak_rate=1.2, **base)) != "HIGH_KNEE_ALTERNATING"
    assert classify_bout(BoutFeatures(peak_rate=1.5, **base)) == "HIGH_KNEE_ALTERNATING"


def test_squat_candidate_splits_descent_hold_and_ascent() -> None:
    grid = np.arange(0.0, 3.01, 0.01)
    q = np.piecewise(
        grid,
        [grid < 1.0, (grid >= 1.0) & (grid <= 2.0), grid > 2.0],
        [lambda value: value, 1.0, lambda value: 3.0 - value],
    )
    jitter = np.zeros_like(grid)
    jitter[(grid >= 1.4) & (grid < 1.5)] = 0.2
    right, left = q + jitter, -q + jitter
    signal = SimpleNamespace(
        dt=0.01,
        grid=grid,
        right=right,
        left=left,
        leg_speed=np.hypot(np.gradient(right, 0.01), np.gradient(left, 0.01)),
    )

    phases = squat_phases(signal, Segment(0.0, 3.0, "SQUAT"))

    assert [phase.label for phase in phases] == [
        "SQUAT_DESCENT",
        "SQUAT_HOLD",
        "SQUAT_ASCENT",
    ]
    assert phases[0].end == pytest.approx(1.0, abs=0.1)
    assert phases[1].start == phases[0].end
    assert phases[1].end == pytest.approx(2.0, abs=0.1)
    assert phases[2].start == phases[1].end


def test_squat_without_hold_changes_phase_at_height_peak() -> None:
    grid = np.arange(0.0, 2.01, 0.01)
    q = 1.0 - abs(grid - 1.0)
    signal = SimpleNamespace(
        dt=0.01,
        grid=grid,
        right=q,
        left=-q,
        leg_speed=np.hypot(np.gradient(q, 0.01), np.gradient(-q, 0.01)),
    )

    phases = squat_phases(signal, Segment(0.0, 2.0, "SQUAT"))

    assert [phase.label for phase in phases] == ["SQUAT_DESCENT", "SQUAT_ASCENT"]
    assert phases[0].end == pytest.approx(1.0, abs=0.05)
    assert phases[1].start == phases[0].end


def test_squat_hold_can_reach_candidate_end() -> None:
    grid = np.arange(0.0, 2.01, 0.01)
    q = np.minimum(grid, 1.0)
    signal = SimpleNamespace(
        dt=0.01,
        grid=grid,
        right=q,
        left=-q,
        leg_speed=np.hypot(np.gradient(q, 0.01), np.gradient(-q, 0.01)),
    )

    phases = squat_phases(signal, Segment(0.0, 2.0, "SQUAT"))

    assert [phase.label for phase in phases] == ["SQUAT_DESCENT", "SQUAT_HOLD"]
    assert phases[-1].end <= grid[-1]


def test_refinement_preserves_shared_squat_phase_boundaries() -> None:
    grid = np.arange(0.0, 5.01, 0.01)
    motion = np.zeros_like(grid)
    motion[(grid >= 0.8) & (grid < 4.2)] = 2.0
    signal = SimpleNamespace(dt=0.01, grid=grid, motion=motion, motion_threshold=1.0)
    segments = [
        Segment(1.0, 2.0, "SQUAT_DESCENT"),
        Segment(2.0, 3.0, "SQUAT_HOLD"),
        Segment(3.0, 4.0, "SQUAT_ASCENT"),
    ]

    refined = refine_activity_edges(signal, segments)

    assert [(item.start, item.end) for item in refined] == pytest.approx(
        [(0.8, 2.0), (2.0, 3.0), (3.0, 4.2)]
    )


def test_t05_terrain_uses_relative_pitch_and_marks_angle_for_review() -> None:
    grid = np.arange(0.0, 20.01, 0.01)
    pitch = np.where(grid < 18.0, 5.0, 0.0)
    signal = SimpleNamespace(dt=0.01, grid=grid, pitch=pitch)
    conditions = [
        {"index": 0, "start": 1.0, "end": 4.0, "rough_start": 1.0, "rough_end": 4.0, "pitch_median": 0.0},
        {"index": 1, "start": 5.0, "end": 8.0, "rough_start": 5.0, "rough_end": 8.0, "pitch_median": 0.1},
        {"index": 2, "start": 9.0, "end": 12.0, "rough_start": 9.0, "rough_end": 12.0, "pitch_median": 3.0},
        {"index": 3, "start": 13.0, "end": 16.0, "rough_start": 13.0, "rough_end": 18.0, "pitch_median": 4.0},
    ]

    terrain, flags = treadmill_terrain(signal, conditions)

    assert not flags
    assert terrain[0].label == "INCLINE"
    assert terrain[0].start == pytest.approx(8.0)
    assert terrain[0].end == pytest.approx(18.0, abs=0.3)
    assert all("待人工复核" in item["condition_note"] for item in conditions[2:])


def test_validation_reports_each_subject_f1_and_interval_iou(tmp_path: Path) -> None:
    reference = [
        event("P01", "T01", 0.0, "STILL"),
        event("P01", "T01", 2.0, "WALKING"),
        event("P01", "T01", 8.0, "STILL"),
        event("P02", "T01", 0.0, "STILL"),
        event("P02", "T01", 3.0, "WALKING"),
        event("P02", "T01", 7.0, "STILL"),
    ]
    generated = [
        event("P01", "T01", 0.0, "STILL"),
        event("P01", "T01", 2.1, "WALKING"),
        event("P01", "T01", 7.9, "STILL"),
        *reference[3:],
    ]
    reference_path = tmp_path / "reference.csv"
    write_events(reference_path, reference)

    report = validate(
        generated,
        reference_path,
        None,
        trials={"T01"},
        end_timestamps={
            "P01_S01_T01_v2.csv": "10.00",
            "P02_S01_T01_v2.csv": "10.00",
        },
    )

    assert report["metrics"]["passed"]
    assert report["batch"]["all_subjects_passed"]
    assert set(report["per_subject"]) == {"P01", "P02"}
    assert report["classification"]["activity"]["labels"]["WALKING"]["f1"] > 0.97
    assert len(report["files"]["P01_S01_T01_v2.csv"]["interval_iou"]) == 3


def test_input_files_can_limit_regression_to_t01_t02(tmp_path: Path) -> None:
    for trial in ("T01", "T02", "T03"):
        (tmp_path / f"P04_S01_{trial}_.csv").touch()

    paths = input_files(tmp_path, None, {"T01", "T02"})

    assert [path.name for path in paths] == ["P04_S01_T01_.csv", "P04_S01_T02_.csv"]


def test_trial_calibration_searches_for_its_own_quiet_window() -> None:
    signal = Signal.__new__(Signal)
    signal.grid = np.arange(0.0, 8.01, signal.dt)
    signal.gaps = []
    signal.motion = np.full_like(signal.grid, 15.0)
    signal.local_motion = np.full_like(signal.grid, 10.0)
    quiet = (signal.grid >= 4.0) & (signal.grid < 6.0)
    signal.motion[quiet] = 0.2
    signal.local_motion[quiet] = 0.3

    motion_threshold, local_threshold, calibration = signal._calibrate()

    assert calibration["window"]["source"] == "search"
    assert calibration["window"]["start_s"] == pytest.approx(4.0, abs=0.02)
    assert motion_threshold == 7.1
    assert local_threshold == 5.0


def test_stair_pause_requires_quiet_legs_without_alternating_steps() -> None:
    grid = np.arange(0.0, 6.01, 0.01)
    leg_speed = np.ones_like(grid)
    local_motion = np.full_like(grid, 10.0)
    pause = (grid >= 2.0) & (grid < 2.4)
    leg_speed[pause] = 0.1
    local_motion[pause] = 1.0
    signal = SimpleNamespace(
        grid=grid,
        leg_speed=leg_speed,
        local_motion=local_motion,
        local_threshold=5.0,
        gaps=[],
    )
    stairs = [Segment(0.5, 5.5, "ASCENT")]

    detected = stair_pauses(signal, stairs, [])
    rejected = stair_pauses(
        signal,
        stairs,
        [Step(2.1, 2.2, "R", 1.0), Step(2.3, 2.35, "L", 1.0)],
    )

    assert len(detected) == 1
    assert detected[0].start == pytest.approx(2.0, abs=0.02)
    assert detected[0].end == pytest.approx(2.4, abs=0.02)
    assert not rejected


def test_external_validation_applies_each_subject_gate(tmp_path: Path) -> None:
    p01_reference = [
        event("P01", "T01", float(index), "STILL" if index % 2 == 0 else "WALKING")
        for index in range(20)
    ]
    p02_reference = [
        event("P02", "T01", 0.0, "STILL"),
        event("P02", "T01", 2.0, "WALKING"),
        event("P02", "T01", 4.0, "STILL"),
    ]
    reference = [*p01_reference, *p02_reference]
    generated = [
        *p01_reference,
        event("P02", "T01", 0.0, "STILL"),
        event("P02", "T01", 2.3, "WALKING"),
        event("P02", "T01", 4.3, "STILL"),
    ]
    reference_path = tmp_path / "reference.csv"
    write_events(reference_path, reference)
    ends = {
        "P01_S01_T01_v2.csv": "21.00",
        "P02_S01_T01_v2.csv": "6.00",
    }

    development = validate(
        generated,
        reference_path,
        None,
        trials={"T01"},
        end_timestamps=ends,
        role="development",
    )
    external = validate(
        generated,
        reference_path,
        None,
        trials={"T01"},
        end_timestamps=ends,
        role="external",
    )

    assert development["metrics"]["passed"]
    assert not development["batch"]["all_subjects_passed"]
    assert external["batch"]["subject_gate_applied"]
    assert not external["metrics"]["passed"]
    assert "failure_policy" in external
