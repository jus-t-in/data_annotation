"""Aggregate CSV repository, audit report, locking, and recoverable paired save."""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import socket
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .constants import APP_NAME, OUTPUT_FIELDS, REPORT_SCHEMA_VERSION, VERSION
from .label_schema import DEFAULT_V2_SCHEMA, DEFAULT_V3_SCHEMA
from .model import AnnotationDocument, ConfirmationKind, Provenance, ReviewIssue, Severity
from .labels import LabelCatalog, LabelCatalogError
from .recovery import file_hash
from .trial import TrialData


class AnnotationStorageError(RuntimeError):
    pass


class AnnotationConflictError(AnnotationStorageError):
    pass


class AnnotationLockError(AnnotationStorageError):
    pass


def report_path_for(annotation_path: Path) -> Path:
    return annotation_path.with_suffix(".report.json")


class CooperativeLock:
    def __init__(self, target: Path, source: Path):
        self.path = Path(f"{target}.lock")
        self.source = source
        self.token = str(uuid.uuid4())
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "app": APP_NAME,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "source": str(self.source),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "token": self.token,
        }
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            owner = ""
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                owner = f"（{data.get('host', '?')} PID {data.get('pid', '?')}）"
                pid = int(data.get("pid", -1))
                if data.get("host") == socket.gethostname() and not self._pid_alive(pid):
                    self.path.unlink()
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                else:
                    raise AnnotationLockError(f"标注文件正被其他编辑器占用{owner}：{self.path}") from exc
            except (OSError, ValueError):
                raise AnnotationLockError(f"标注文件正被其他编辑器占用{owner}：{self.path}") from exc
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.acquired = True

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        self.acquired = False


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
    writer.writeheader()
    writer.writerows({field: row.get(field, "") for field in OUTPUT_FIELDS} for row in rows)
    return ("\ufeff" + stream.getvalue()).encode("utf-8")


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class AnnotationRepository:
    def __init__(self, target: Path, trial: TrialData, *, acquire_lock: bool = True):
        self.target = target.expanduser().resolve()
        if self.target.suffix.lower() != ".csv":
            raise AnnotationStorageError("标注文件必须使用 .csv 扩展名")
        if self.target == trial.path:
            raise AnnotationStorageError("标注文件不能覆盖原始试次 CSV")
        self.trial = trial
        self.catalog_path = self.target.parent / "label_catalog.json"
        try:
            self.catalog = LabelCatalog.load(self.catalog_path)
        except LabelCatalogError as exc:
            raise AnnotationStorageError(str(exc)) from exc
        self.report_path = report_path_for(self.target)
        self.lock = CooperativeLock(self.target, trial.path)
        if acquire_lock:
            self.lock.acquire()
        self._recover_transaction()
        self.expected_target_hash = file_hash(self.target)
        self.expected_report_hash = file_hash(self.report_path)
        self._rows = self._read_rows()
        self._report = self._read_report()
        try:
            if not self.trial.is_v3 and self.catalog.discover_rows(self._rows):
                self.catalog.save(self.catalog_path)
        except (OSError, ValueError) as exc:
            self.lock.release()
            raise AnnotationStorageError(f"无法更新标签目录：{self.catalog_path}") from exc

    def close(self) -> None:
        self.lock.release()

    def _read_rows(self) -> list[dict[str, str]]:
        if not self.target.exists():
            return []
        try:
            with self.target.open(encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                if tuple(reader.fieldnames or ()) != OUTPUT_FIELDS:
                    raise AnnotationStorageError(
                        "标注文件字段必须严格为：" + ",".join(OUTPUT_FIELDS)
                    )
                rows = []
                for line, row in enumerate(reader, 2):
                    if None in row or any(row.get(field) is None for field in OUTPUT_FIELDS):
                        raise AnnotationStorageError(f"标注文件第 {line} 行字段数量不正确")
                    rows.append({field: row[field] for field in OUTPUT_FIELDS})
                return rows
        except UnicodeDecodeError as exc:
            raise AnnotationStorageError("标注文件不是有效的 UTF-8/UTF-8 BOM 编码") from exc

    def _read_report(self) -> dict:
        if not self.report_path.exists():
            return {}
        try:
            value = json.loads(self.report_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnnotationStorageError(f"配套报告无法解析：{self.report_path}") from exc
        return value if isinstance(value, dict) else {}

    @property
    def current_rows(self) -> list[dict[str, str]]:
        return [row for row in self._rows if row["input_file"] == self.trial.path.name]

    def load_document(self) -> AnnotationDocument | None:
        rows = self.current_rows
        if not rows:
            return None
        for row in rows:
            if row["session_id"] != self.trial.session_id or row["trial_id"] != self.trial.trial_id:
                raise AnnotationStorageError("当前 input_file 的 session_id/trial_id 与原始试次不一致")
        report_valid = (
            self._report.get("schema") == "youbu-annotation-report"
            and self._report.get("schema_version") == REPORT_SCHEMA_VERSION
            and self._report.get("annotation_file", {}).get("sha256") == self.expected_target_hash
        )
        entry = self._report.get("trials", {}).get(self.trial.path.name, {}) if report_valid else {}
        if entry.get("source", {}).get("sha256") == self.trial.source_hash and "document" in entry:
            return AnnotationDocument.from_dict(self.trial, entry["document"], catalog=self.catalog)

        issues = []
        if self.report_path.exists():
            issues.append(
                ReviewIssue(
                    "report_unusable",
                    "配套报告与当前标注或原始试次不匹配，已按 CSV 来源不明方式导入",
                    Severity.WARNING,
                )
            )
        schema = DEFAULT_V3_SCHEMA if self.trial.is_v3 else DEFAULT_V2_SCHEMA
        return AnnotationDocument.from_rows(
            self.trial,
            rows,
            issues,
            label_schema=schema,
            catalog=self.catalog,
        )

    def _controlled_note(self, event) -> str:
        components = [
            item
            for item in [*self._document.boundaries, *self._document.confirmations]
            if item.id in event.component_ids
        ]
        originals = {
            (item.original or {}).get("notes", "")
            for item in components
            if (item.original or {}).get("notes")
        }
        if event.provenance is Provenance.IMPORTED_UNKNOWN and len(originals) == 1 and not event.user_note:
            return originals.pop()
        source = {
            Provenance.AUTO: "自动生成未改",
            Provenance.ADJUSTED: "自动生成后人工调整",
            Provenance.MANUAL: "人工新建",
            Provenance.IMPORTED_UNKNOWN: "导入来源不明",
        }[event.provenance]
        kind = {
            "initial": "文件开头状态",
            "boundary": "组合状态边界",
            "stitch_initial": "接缝初始化事件",
            ConfirmationKind.STAIR_SECOND_STEP.value: "楼梯第二步确认",
            ConfirmationKind.TRIAL_END.value: "试次收尾确认",
            ConfirmationKind.GAP_RECONFIRMATION.value: "断档后重新确认",
        }[event.kind]
        note = f"近似：{source}的{kind}"
        return f"{note}；{event.user_note}" if event.user_note else note

    def document_rows(self, document: AnnotationDocument) -> list[dict[str, str]]:
        self._document = document
        rows = []
        for event in document.composed_events():
            rows.append(
                {
                    "session_id": self.trial.session_id,
                    "trial_id": self.trial.trial_id,
                    "input_file": self.trial.path.name,
                    "timestamp": self.trial.timestamp(event.sample_index),
                    "activity_truth": event.activity,
                    "terrain_truth": event.terrain,
                    "notes": self._controlled_note(event),
                }
            )
        return rows

    def _aggregate_rows(self, replacements: list[dict[str, str]]) -> list[dict[str, str]]:
        positions = [index for index, row in enumerate(self._rows) if row["input_file"] == self.trial.path.name]
        insertion = positions[0] if positions else len(self._rows)
        remaining = [row for row in self._rows if row["input_file"] != self.trial.path.name]
        if positions:
            insertion -= sum(1 for index in positions if index < insertion)
        return remaining[:insertion] + replacements + remaining[insertion:]

    def _report_bytes(self, document: AnnotationDocument, csv_hash: str, saved_at: str) -> bytes:
        report = copy.deepcopy(self._report) if self._report.get("schema") == "youbu-annotation-report" else {}
        state = document._state_dict()
        document_data = document.to_dict()
        document_data["baseline"] = copy.deepcopy(state)
        report.update(
            {
                "schema": "youbu-annotation-report",
                "schema_version": REPORT_SCHEMA_VERSION,
                "app": {"name": APP_NAME, "version": VERSION},
                "annotation_file": {
                    "path": str(self.target),
                    "sha256": csv_hash,
                    "saved_at": saved_at,
                },
            }
        )
        trials = report.setdefault("trials", {})
        trials[self.trial.path.name] = {
            "file": self.trial.file_info.to_dict(),
            "source": {
                "path": str(self.trial.path),
                "sha256": self.trial.source_hash,
                "samples": len(self.trial.seconds),
                "duration_seconds": self.trial.duration,
                "gaps": [vars(gap) for gap in self.trial.gaps],
            },
            "saved_at": saved_at,
            "audit": copy.deepcopy(document.detail.get("audit", {})),
            "suggestion": copy.deepcopy(document.detail.get("suggestion")),
            "legacy_qa": copy.deepcopy(document.detail.get("legacy_qa", [])),
            "gap_contract": copy.deepcopy(document.detail.get("gap_contract", {})),
            "gap_declarations": document.gap_declarations(),
            "batch_note": document.detail.get("batch_note", ""),
            "document": document_data,
            "events": [
                {
                    "id": event.id,
                    "timestamp": self.trial.timestamp(event.sample_index),
                    "kind": event.kind,
                    "component_ids": list(event.component_ids),
                    "provenance": event.provenance.value,
                }
                for event in document.composed_events()
            ],
            "statistics": document.statistics(),
        }
        return (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def save(self, document: AnnotationDocument) -> None:
        if document.is_read_only or self.trial.is_read_only:
            raise AnnotationStorageError("源记录为只读，不能正式保存")
        if not document.ready_to_save:
            raise AnnotationStorageError("标注尚未满足正式保存条件")
        if file_hash(self.target) != self.expected_target_hash:
            raise AnnotationConflictError("标注 CSV 已被外部修改，已拒绝覆盖")
        if file_hash(self.report_path) != self.expected_report_hash:
            raise AnnotationConflictError("配套报告已被外部修改，已拒绝覆盖")
        rows = self._aggregate_rows(self.document_rows(document))
        csv_content = _csv_bytes(rows)
        csv_hash = _hash_bytes(csv_content)
        saved_at = datetime.now(timezone.utc).isoformat()
        report_content = self._report_bytes(document, csv_hash, saved_at)
        self.catalog = document.catalog
        try:
            self.catalog.save(self.catalog_path)
        except OSError as exc:
            raise AnnotationStorageError(f"标签目录保存失败：{self.catalog_path}") from exc
        self._write_pair(csv_content, report_content)
        self._rows = rows
        self._report = json.loads(report_content.decode("utf-8"))
        self.expected_target_hash = csv_hash
        self.expected_report_hash = _hash_bytes(report_content)
        document.mark_saved()

    @property
    def _transaction_path(self) -> Path:
        return Path(f"{self.target}.transaction.json")

    def _stage(self, path: Path, content: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return Path(name)

    def _backup(self, path: Path) -> None:
        backup = Path(f"{path}.bak")
        if not path.exists():
            backup.unlink(missing_ok=True)
            return
        staged = self._stage(backup, path.read_bytes())
        os.replace(staged, backup)

    def _write_pair(self, csv_content: bytes, report_content: bytes) -> None:
        csv_temp = self._stage(self.target, csv_content)
        report_temp = self._stage(self.report_path, report_content)
        self._backup(self.target)
        self._backup(self.report_path)
        transaction = {
            "old": {
                "csv": file_hash(self.target),
                "report": file_hash(self.report_path),
            },
            "new": {
                "csv": _hash_bytes(csv_content),
                "report": _hash_bytes(report_content),
            },
        }
        transaction_temp = self._stage(
            self._transaction_path,
            (json.dumps(transaction, indent=2) + "\n").encode("utf-8"),
        )
        os.replace(transaction_temp, self._transaction_path)
        _fsync_directory(self.target.parent)
        try:
            os.replace(report_temp, self.report_path)
            os.replace(csv_temp, self.target)
            _fsync_directory(self.target.parent)
            self._transaction_path.unlink()
        except Exception:
            self._restore_backups(transaction["old"])
            raise
        finally:
            csv_temp.unlink(missing_ok=True)
            report_temp.unlink(missing_ok=True)

    def _restore_backups(self, old: dict) -> None:
        for path, expected in ((self.target, old.get("csv")), (self.report_path, old.get("report"))):
            backup = Path(f"{path}.bak")
            if expected is None:
                path.unlink(missing_ok=True)
            elif file_hash(backup) == expected:
                staged = self._stage(path, backup.read_bytes())
                os.replace(staged, path)
            else:
                raise AnnotationStorageError(f"无法从配对备份恢复：{backup}")
        self._transaction_path.unlink(missing_ok=True)
        _fsync_directory(self.target.parent)

    def _recover_transaction(self) -> None:
        if not self._transaction_path.exists():
            return
        try:
            transaction = json.loads(self._transaction_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AnnotationStorageError("保存事务记录损坏，需要人工检查标注与备份文件") from exc
        new = transaction.get("new", {})
        if file_hash(self.target) == new.get("csv") and file_hash(self.report_path) == new.get("report"):
            self._transaction_path.unlink()
            return
        self._restore_backups(transaction.get("old", {}))
