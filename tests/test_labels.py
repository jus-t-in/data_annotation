from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.axes import Axes

from youbu_annotation.constants import state_color, valid_state
from youbu_annotation.labels import LabelCatalog
from youbu_annotation.model import AnnotationDocument, Boundary, Provenance, Track
from youbu_annotation.renderer import render_annotation_png
from youbu_annotation.trial import TrialData


class _Trial:
    seconds = (0.0,)
    source_hash = "fixture"
    path = Path("P01_S01_T05_.csv")

    def timestamp(self, _index: int) -> str:
        return "0.0"


def test_custom_labels_round_trip_and_soft_disable(tmp_path: Path) -> None:
    path = tmp_path / "label_catalog.json"
    catalog = LabelCatalog.default()

    catalog.add(Track.ACTIVITY, "RUNNING")
    catalog.add(Track.TERRAIN, "INCLINE")
    catalog.disable(Track.ACTIVITY, "RUNNING")
    catalog.save(path)

    restored = LabelCatalog.load(path)
    assert not restored.is_enabled(Track.ACTIVITY, "RUNNING")
    assert restored.is_enabled(Track.TERRAIN, "INCLINE")

    restored.enable(Track.ACTIVITY, "RUNNING")
    assert restored.is_enabled(Track.ACTIVITY, "RUNNING")


def test_discovered_custom_terrain_accepts_unseen_combinations() -> None:
    catalog = LabelCatalog.default()
    changed = catalog.discover_rows(
        [{"activity_truth": "OTHER", "terrain_truth": "INCLINE"}]
    )

    assert changed
    assert valid_state("OTHER", "INCLINE", catalog)
    assert not valid_state("BEND", "ASCENT", catalog)


def test_document_keeps_disabled_historical_label() -> None:
    trial = _Trial()
    catalog = LabelCatalog.default()
    catalog.add(Track.TERRAIN, "INCLINE")
    catalog.disable(Track.TERRAIN, "INCLINE")
    document = AnnotationDocument(
        trial,
        [
            Boundary(Track.ACTIVITY, 0, "OTHER", Provenance.MANUAL),
            Boundary(Track.TERRAIN, 0, "INCLINE", Provenance.MANUAL),
        ],
        catalog=catalog,
    )

    assert document.structural_errors() == []


def test_custom_state_uses_a_real_overlay_color(tmp_path: Path, monkeypatch) -> None:
    seconds = np.array([0.0, 1.0, 2.0])
    zero = np.zeros_like(seconds)
    trial = TrialData(
        path=Path("P01_S01_T05_.csv"),
        session_id="P01_S01",
        trial_id="T05",
        time_column="Elapsed (s)",
        seconds=seconds,
        timestamps=["0.0", "1.0", "2.0"],
        start_datetime=None,
        channels={
            "display/left": zero,
            "display/right": zero,
            "display/pitch": zero,
            "display/motion": zero,
            "display/impact": zero,
            "display/left_raw": zero,
            "display/right_raw": zero,
            "display/pitch_raw": zero,
        },
        ignored_columns=(),
        duplicate_rows=0,
        gaps=[],
        warnings=[],
        source_hash="fixture",
    )
    catalog = LabelCatalog.default()
    catalog.add(Track.TERRAIN, "INCLINE")
    document = AnnotationDocument(
        trial,
        [
            Boundary(Track.ACTIVITY, 0, "WALKING", Provenance.MANUAL),
            Boundary(Track.TERRAIN, 0, "LEVEL", Provenance.MANUAL),
            Boundary(Track.TERRAIN, 1, "INCLINE", Provenance.MANUAL),
        ],
        catalog=catalog,
    )
    colors = []
    original = Axes.axvspan

    def capture(self, *args, **kwargs):
        colors.append(str(kwargs.get("color", "")).upper())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Axes, "axvspan", capture)
    render_annotation_png(trial, document, tmp_path / "custom.png")

    assert "#607D8B" not in colors
    assert state_color("WALKING", "INCLINE") != state_color("WALKING", "LEVEL")
