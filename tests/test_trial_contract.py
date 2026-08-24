from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from youbu_annotation import auto_annotate as legacy
from youbu_annotation.trial import TrialData, TrialValidationError


SENSOR_COLUMNS = (
    legacy.RIGHT_COLUMN,
    legacy.LEFT_COLUMN_ALT,
    legacy.PITCH_COLUMN,
    *legacy.GYRO_COLUMNS,
    *legacy.ACCEL_COLUMNS,
)


def write_trial(path: Path, *, omit: str | None = None) -> None:
    fields = [legacy.ELAPSED_COLUMN, *SENSOR_COLUMNS]
    if omit:
        fields.remove(omit)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for elapsed, right in ((0.0, 2.0), (0.0, 4.0), (1.0, 8.0)):
            row = {name: "0" for name in fields}
            row[legacy.ELAPSED_COLUMN] = str(elapsed)
            if legacy.RIGHT_COLUMN in row:
                row[legacy.RIGHT_COLUMN] = str(right)
            writer.writerow(row)


def test_trial_loader_keeps_fixed_sensor_columns_and_normalizes_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "P01_S01_T01_fixture.csv"
    write_trial(path)

    trial = TrialData.load(path)

    assert set(SENSOR_COLUMNS).issubset(trial.channels)
    assert np.array_equal(trial.seconds, np.array([0.0, 1.0]))
    assert trial.channels[legacy.RIGHT_COLUMN].tolist() == [3.0, 8.0]
    assert trial.duplicate_rows == 1


def test_trial_loader_rejects_a_missing_fixed_sensor_column(tmp_path: Path) -> None:
    path = tmp_path / "P01_S01_T01_fixture.csv"
    write_trial(path, omit=legacy.PITCH_COLUMN)

    with pytest.raises(TrialValidationError, match=legacy.PITCH_COLUMN):
        TrialData.load(path)
