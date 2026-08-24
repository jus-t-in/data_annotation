"""Crash-recovery drafts stored below the XDG state directory."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .labels import LabelCatalog
from .constants import APP_NAME, VERSION
from .model import AnnotationDocument
from .trial import TrialData


class RecoveryConflictError(RuntimeError):
    pass


def file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class RecoveryStore:
    def __init__(self, trial: TrialData, target: Path, catalog: LabelCatalog | None = None):
        state_home = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
        self.directory = state_home / "youbu-annotation" / "recovery"
        identity = f"{trial.path.resolve()}\0{target.resolve()}".encode()
        self.path = self.directory / f"{hashlib.sha256(identity).hexdigest()}.json"
        self.trial = trial
        self.target = target.resolve()
        self.catalog = catalog

    def save(self, document: AnnotationDocument, target_hash: str | None, report_hash: str | None) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "app": {"name": APP_NAME, "version": VERSION},
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "source": {"path": str(self.trial.path), "sha256": self.trial.source_hash},
            "target": {
                "path": str(self.target),
                "sha256": target_hash,
                "report_sha256": report_hash,
            },
            "document": document.to_dict(),
        }
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return self.path

    def load(self, *, check_target: bool = True) -> AnnotationDocument | None:
        if not self.path.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        source = payload.get("source", {})
        target = payload.get("target", {})
        if source.get("path") != str(self.trial.path) or source.get("sha256") != self.trial.source_hash:
            raise RecoveryConflictError("恢复草稿对应的原始试次已变化")
        if target.get("path") != str(self.target):
            raise RecoveryConflictError("恢复草稿对应的标注文件不一致")
        if check_target and target.get("sha256") != file_hash(self.target):
            raise RecoveryConflictError("恢复草稿创建后，标注文件已被外部修改")
        report_path = self.target.with_suffix(".report.json")
        if check_target and target.get("report_sha256") != file_hash(report_path):
            raise RecoveryConflictError("恢复草稿创建后，配套报告已被外部修改")
        return AnnotationDocument.from_dict(self.trial, payload["document"], catalog=self.catalog)

    def metadata(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
