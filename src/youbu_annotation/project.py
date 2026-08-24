"""SQLite-backed annotation project with immutable published history."""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .label_schema import DEFAULT_V2_SCHEMA, LabelSchema
from .model import AnnotationDocument
from .trial import TrialData, sha256_file

MANIFEST_NAME = "project.json"
DATABASE_NAME = "project.sqlite3"
PROJECT_SCHEMA_VERSION = 1
DATABASE_VERSION = 1


class ProjectError(RuntimeError):
    pass


class ProjectConflictError(ProjectError):
    pass


class ProjectLockError(ProjectError):
    pass


class ProjectSourceChangedError(ProjectError):
    pass


@dataclass(frozen=True)
class TrialRecord:
    id: str
    subject_id: str
    session_id: str
    trial_id: str
    source_path: Path
    source_hash: str
    input_file: str
    schema_id: str
    schema_version: int


@dataclass(frozen=True)
class RevisionRecord:
    id: str
    trial_record_id: str
    parent_id: str | None
    schema_id: str
    schema_version: int
    source_hash: str
    annotator_id: str
    created_at: str


class ProjectLock:
    def __init__(self, root: Path):
        self.path = root / ".youbu-project.lock"
        self.token = str(uuid.uuid4())
        self.acquired = False

    def acquire(self) -> None:
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": _now(),
            "token": self.token,
        }
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            try:
                owner = json.loads(self.path.read_text(encoding="utf-8"))
                pid = int(owner.get("pid", -1))
                if owner.get("host") == socket.gethostname() and not _pid_alive(pid):
                    self.path.unlink()
                    descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                else:
                    raise ProjectLockError(
                        f"项目正被其他进程占用（{owner.get('host', '?')} PID {owner.get('pid', '?')}）"
                    ) from exc
            except (OSError, ValueError, json.JSONDecodeError) as error:
                raise ProjectLockError(f"项目锁无法读取：{self.path}") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("token") == self.token:
                self.path.unlink(missing_ok=True)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        self.acquired = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _required_text(value: str, field_name: str) -> str:
    result = value.strip()
    if not result:
        raise ProjectError(f"{field_name}不能为空")
    if len(result) > 100 or any(ord(char) < 32 for char in result):
        raise ProjectError(f"{field_name}必须为不超过 100 字的单行文本")
    return result


class AnnotationProject:
    def __init__(self, root: Path, manifest: dict, lock: ProjectLock, connection: sqlite3.Connection):
        self.root = root
        self.manifest = manifest
        self.project_id = manifest["project_id"]
        self._lock = lock
        self._connection = connection

    @classmethod
    def create(cls, root: Path, *, name: str) -> "AnnotationProject":
        root = root.expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / MANIFEST_NAME
        database_path = root / DATABASE_NAME
        if manifest_path.exists() or database_path.exists():
            raise ProjectError("目标目录已经包含标注项目")
        project_id = str(uuid.uuid4())
        manifest = {
            "schema": "youbu-annotation-project",
            "schema_version": PROJECT_SCHEMA_VERSION,
            "project_id": project_id,
            "database": DATABASE_NAME,
        }
        lock = ProjectLock(root)
        lock.acquire()
        try:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            connection = cls._connect(database_path)
            cls._initialize(connection, project_id, _required_text(name, "项目名称"))
            return cls(root, manifest, lock, connection)
        except Exception:
            lock.release()
            raise

    @classmethod
    def open(cls, root: Path) -> "AnnotationProject":
        root = root.expanduser().resolve()
        manifest_path = root / MANIFEST_NAME
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectError(f"无法读取项目清单：{manifest_path}") from exc
        if (
            manifest.get("schema") != "youbu-annotation-project"
            or manifest.get("schema_version") != PROJECT_SCHEMA_VERSION
            or not manifest.get("project_id")
        ):
            raise ProjectError("项目清单格式或版本不受支持")
        database_name = manifest.get("database")
        if not isinstance(database_name, str) or Path(database_name).name != database_name:
            raise ProjectError("项目数据库路径无效")
        database_path = root / database_name
        if not database_path.is_file():
            raise ProjectError(f"项目数据库不存在：{database_path}")
        lock = ProjectLock(root)
        lock.acquire()
        try:
            connection = cls._connect(database_path)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            stored_id = connection.execute("SELECT value FROM project_metadata WHERE key = 'project_id'").fetchone()
            if version != DATABASE_VERSION or stored_id is None or stored_id[0] != manifest["project_id"]:
                raise ProjectError("项目数据库版本或项目 ID 与清单不一致")
            return cls(root, manifest, lock, connection)
        except Exception:
            lock.release()
            raise

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @classmethod
    def _initialize(cls, connection: sqlite3.Connection, project_id: str, name: str) -> None:
        if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
            raise ProjectError("新项目数据库不是空数据库")
        schema_id = str(uuid.uuid4())
        initial_schema = LabelSchema(
            schema_id=schema_id,
            version=1,
            activity_labels=DEFAULT_V2_SCHEMA.activity_labels,
            terrain_labels=DEFAULT_V2_SCHEMA.terrain_labels,
            states=DEFAULT_V2_SCHEMA.states,
        )
        draft = LabelSchema(
            schema_id=schema_id,
            version=2,
            activity_labels=initial_schema.activity_labels,
            terrain_labels=initial_schema.terrain_labels,
            states=initial_schema.states,
        )
        connection.executescript(
            """
            CREATE TABLE project_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE schema_versions (
                schema_id TEXT NOT NULL,
                version INTEGER NOT NULL CHECK (version >= 1),
                content_hash TEXT NOT NULL,
                schema_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (schema_id, version)
            );
            CREATE TABLE schema_drafts (
                schema_id TEXT PRIMARY KEY,
                base_version INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                schema_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE trials (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                trial_id TEXT NOT NULL,
                source_path TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                input_file TEXT NOT NULL,
                schema_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE (subject_id, session_id, trial_id),
                FOREIGN KEY (schema_id, schema_version)
                    REFERENCES schema_versions(schema_id, version)
            );
            CREATE TABLE annotation_revisions (
                id TEXT PRIMARY KEY,
                trial_record_id TEXT NOT NULL REFERENCES trials(id),
                parent_id TEXT REFERENCES annotation_revisions(id),
                schema_id TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                source_hash TEXT NOT NULL,
                document_json TEXT NOT NULL,
                annotator_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (schema_id, schema_version)
                    REFERENCES schema_versions(schema_id, version)
            );
            CREATE TABLE annotation_heads (
                trial_record_id TEXT PRIMARY KEY REFERENCES trials(id),
                revision_id TEXT NOT NULL REFERENCES annotation_revisions(id)
            );
            CREATE TABLE annotation_drafts (
                trial_record_id TEXT PRIMARY KEY REFERENCES trials(id),
                base_revision_id TEXT REFERENCES annotation_revisions(id),
                document_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TRIGGER immutable_schema_update
            BEFORE UPDATE ON schema_versions BEGIN
                SELECT RAISE(ABORT, 'published schema versions are immutable');
            END;
            CREATE TRIGGER immutable_schema_delete
            BEFORE DELETE ON schema_versions BEGIN
                SELECT RAISE(ABORT, 'published schema versions are immutable');
            END;
            CREATE TRIGGER immutable_revision_update
            BEFORE UPDATE ON annotation_revisions BEGIN
                SELECT RAISE(ABORT, 'annotation revisions are immutable');
            END;
            CREATE TRIGGER immutable_revision_delete
            BEFORE DELETE ON annotation_revisions BEGIN
                SELECT RAISE(ABORT, 'annotation revisions are immutable');
            END;
            """
        )
        created_at = _now()
        with connection:
            connection.executemany(
                "INSERT INTO project_metadata(key, value) VALUES (?, ?)",
                (("project_id", project_id), ("name", name)),
            )
            connection.execute(
                "INSERT INTO schema_versions VALUES (?, ?, ?, ?, ?)",
                (
                    initial_schema.schema_id,
                    initial_schema.version,
                    initial_schema.content_hash,
                    json.dumps(initial_schema.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO schema_drafts VALUES (?, ?, ?, ?, ?)",
                (
                    draft.schema_id,
                    1,
                    draft.content_hash,
                    json.dumps(draft.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO audit_log(action, entity_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
                ("project_created", project_id, json.dumps({"name": name}, ensure_ascii=False), created_at),
            )
            connection.execute(f"PRAGMA user_version = {DATABASE_VERSION}")

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    @property
    def name(self) -> str:
        row = self._connection.execute("SELECT value FROM project_metadata WHERE key = 'name'").fetchone()
        return row[0]

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            self._lock.release()

    def __enter__(self) -> "AnnotationProject":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def schema_draft(self) -> LabelSchema:
        row = self._connection.execute("SELECT schema_json FROM schema_drafts").fetchone()
        if row is None:
            raise ProjectError("项目缺少标签模式草稿")
        return LabelSchema.from_dict(json.loads(row[0]))

    def update_schema_draft(self, schema: LabelSchema, *, expected_hash: str) -> LabelSchema:
        with self._transaction():
            row = self._connection.execute(
                "SELECT schema_id, base_version, content_hash FROM schema_drafts"
            ).fetchone()
            if row is None or row["content_hash"] != expected_hash:
                raise ProjectConflictError("标签模式草稿已被其他页面修改")
            if schema.schema_id != row["schema_id"] or schema.version != row["base_version"] + 1:
                raise ProjectError("标签模式草稿 ID 或版本不匹配")
            self._connection.execute(
                "UPDATE schema_drafts SET content_hash = ?, schema_json = ?, updated_at = ? WHERE schema_id = ?",
                (
                    schema.content_hash,
                    json.dumps(schema.to_dict(), ensure_ascii=False),
                    _now(),
                    schema.schema_id,
                ),
            )
        return schema

    def publish_schema_draft(self, *, expected_hash: str) -> LabelSchema:
        with self._transaction():
            row = self._connection.execute(
                "SELECT schema_id, base_version, content_hash, schema_json FROM schema_drafts"
            ).fetchone()
            if row is None or row["content_hash"] != expected_hash:
                raise ProjectConflictError("标签模式草稿已被其他页面修改")
            schema = LabelSchema.from_dict(json.loads(row["schema_json"]))
            created_at = _now()
            self._connection.execute(
                "INSERT INTO schema_versions VALUES (?, ?, ?, ?, ?)",
                (
                    schema.schema_id,
                    schema.version,
                    schema.content_hash,
                    json.dumps(schema.to_dict(), ensure_ascii=False),
                    created_at,
                ),
            )
            next_draft = LabelSchema(
                schema.schema_id,
                schema.version + 1,
                schema.activity_labels,
                schema.terrain_labels,
                schema.states,
            )
            self._connection.execute(
                "UPDATE schema_drafts SET base_version = ?, content_hash = ?, schema_json = ?, updated_at = ? "
                "WHERE schema_id = ?",
                (
                    schema.version,
                    next_draft.content_hash,
                    json.dumps(next_draft.to_dict(), ensure_ascii=False),
                    created_at,
                    schema.schema_id,
                ),
            )
            self._audit("schema_published", f"{schema.schema_id}:{schema.version}", schema.to_dict(), created_at)
        return schema

    def schema_version(self, schema_id: str, version: int) -> LabelSchema:
        row = self._connection.execute(
            "SELECT schema_json FROM schema_versions WHERE schema_id = ? AND version = ?",
            (schema_id, version),
        ).fetchone()
        if row is None:
            raise ProjectError("标签模式版本不存在")
        return LabelSchema.from_dict(json.loads(row[0]))

    def register_trial(
        self,
        source_path: Path,
        *,
        subject_id: str,
        session_id: str,
        trial_id: str,
    ) -> TrialRecord:
        subject_id = _required_text(subject_id, "受试者 ID")
        session_id = _required_text(session_id, "会话 ID")
        trial_id = _required_text(trial_id, "试次 ID")
        source_path = source_path.expanduser().resolve()
        trial = TrialData.load(source_path, session_id=session_id, trial_id=trial_id)
        schema_row = self._connection.execute(
            "SELECT schema_id, version FROM schema_versions ORDER BY version DESC LIMIT 1"
        ).fetchone()
        record = TrialRecord(
            id=str(uuid.uuid4()),
            subject_id=subject_id,
            session_id=trial.session_id,
            trial_id=trial.trial_id,
            source_path=source_path,
            source_hash=trial.source_hash,
            input_file=source_path.name,
            schema_id=schema_row["schema_id"],
            schema_version=schema_row["version"],
        )
        try:
            with self._transaction():
                self._connection.execute(
                    "INSERT INTO trials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.id,
                        record.subject_id,
                        record.session_id,
                        record.trial_id,
                        str(record.source_path),
                        record.source_hash,
                        record.input_file,
                        record.schema_id,
                        record.schema_version,
                        _now(),
                    ),
                )
                self._audit("trial_registered", record.id, self._trial_payload(record))
        except sqlite3.IntegrityError as exc:
            raise ProjectError("受试者、会话和试次 ID 的组合已经存在") from exc
        return record

    def trials(self) -> list[TrialRecord]:
        rows = self._connection.execute(
            "SELECT id, subject_id, session_id, trial_id, source_path, source_hash, input_file, "
            "schema_id, schema_version FROM trials ORDER BY subject_id, session_id, trial_id"
        ).fetchall()
        return [self._trial_from_row(row) for row in rows]

    def trial(self, record_id: str) -> TrialRecord:
        row = self._connection.execute(
            "SELECT id, subject_id, session_id, trial_id, source_path, source_hash, input_file, "
            "schema_id, schema_version FROM trials WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ProjectError("试次不存在")
        return self._trial_from_row(row)

    def load_trial(self, record_id: str) -> TrialData:
        record = self.trial(record_id)
        if not record.source_path.is_file():
            raise ProjectSourceChangedError(f"原始 CSV 不存在：{record.source_path}")
        if sha256_file(record.source_path) != record.source_hash:
            raise ProjectSourceChangedError("原始 CSV 内容已变化")
        return TrialData.load(
            record.source_path,
            session_id=record.session_id,
            trial_id=record.trial_id,
        )

    def load_document(self, record_id: str) -> tuple[TrialData, AnnotationDocument, str | None]:
        record = self.trial(record_id)
        trial = self.load_trial(record_id)
        head = self._head(record_id)
        if head is None:
            schema = self.schema_version(record.schema_id, record.schema_version)
            return trial, AnnotationDocument.from_rows(trial, [], label_schema=schema), None
        row = self._connection.execute(
            "SELECT document_json FROM annotation_revisions WHERE id = ?",
            (head,),
        ).fetchone()
        return trial, AnnotationDocument.from_dict(trial, json.loads(row[0])), head

    def save_draft(
        self,
        record_id: str,
        document: AnnotationDocument,
        *,
        expected_revision_id: str | None,
    ) -> None:
        if self._head(record_id) != expected_revision_id:
            raise ProjectConflictError("当前标注修订已变化，草稿未覆盖")
        with self._transaction():
            self._connection.execute(
                "INSERT INTO annotation_drafts VALUES (?, ?, ?, ?) "
                "ON CONFLICT(trial_record_id) DO UPDATE SET "
                "base_revision_id = excluded.base_revision_id, document_json = excluded.document_json, "
                "updated_at = excluded.updated_at",
                (
                    record_id,
                    expected_revision_id,
                    json.dumps(document.to_dict(), ensure_ascii=False),
                    _now(),
                ),
            )

    def load_draft(self, record_id: str) -> tuple[AnnotationDocument, str | None] | None:
        row = self._connection.execute(
            "SELECT base_revision_id, document_json FROM annotation_drafts WHERE trial_record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        trial = self.load_trial(record_id)
        return AnnotationDocument.from_dict(trial, json.loads(row["document_json"])), row["base_revision_id"]

    def commit_revision(
        self,
        record_id: str,
        document: AnnotationDocument,
        *,
        expected_revision_id: str | None,
    ) -> RevisionRecord:
        if not document.ready_to_save:
            raise ProjectError("标注尚未满足正式保存条件")
        record = self.trial(record_id)
        current_hash = sha256_file(record.source_path) if record.source_path.is_file() else None
        if current_hash != record.source_hash or document.trial.source_hash != record.source_hash:
            raise ProjectSourceChangedError("原始 CSV 内容已变化，已阻止正式保存")
        schema = self.schema_version(record.schema_id, record.schema_version)
        if document.label_schema.content_hash != schema.content_hash:
            raise ProjectError("标注文档的标签模式与试次登记不一致")
        reviewed = document.reviewed or {}
        revision = RevisionRecord(
            id=str(uuid.uuid4()),
            trial_record_id=record_id,
            parent_id=expected_revision_id,
            schema_id=schema.schema_id,
            schema_version=schema.version,
            source_hash=record.source_hash,
            annotator_id=reviewed["annotator_id"],
            created_at=_now(),
        )
        document_data = document.to_dict()
        document_data["baseline"] = document._state_dict()
        with self._transaction():
            if self._head(record_id) != expected_revision_id:
                raise ProjectConflictError("当前标注修订已变化，请重新加载后再保存")
            self._connection.execute(
                "INSERT INTO annotation_revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    revision.id,
                    revision.trial_record_id,
                    revision.parent_id,
                    revision.schema_id,
                    revision.schema_version,
                    revision.source_hash,
                    json.dumps(document_data, ensure_ascii=False),
                    revision.annotator_id,
                    revision.created_at,
                ),
            )
            self._connection.execute(
                "INSERT INTO annotation_heads VALUES (?, ?) "
                "ON CONFLICT(trial_record_id) DO UPDATE SET revision_id = excluded.revision_id",
                (record_id, revision.id),
            )
            self._connection.execute("DELETE FROM annotation_drafts WHERE trial_record_id = ?", (record_id,))
            self._audit("annotation_revision_committed", revision.id, vars(revision), revision.created_at)
        document.mark_saved()
        return revision

    def revisions(self, record_id: str) -> list[RevisionRecord]:
        rows = self._connection.execute(
            "SELECT id, trial_record_id, parent_id, schema_id, schema_version, source_hash, "
            "annotator_id, created_at FROM annotation_revisions WHERE trial_record_id = ? ORDER BY rowid",
            (record_id,),
        ).fetchall()
        return [RevisionRecord(**dict(row)) for row in rows]

    def revision_document(self, revision_id: str) -> AnnotationDocument:
        row = self._connection.execute(
            "SELECT trial_record_id, document_json FROM annotation_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        if row is None:
            raise ProjectError("标注修订不存在")
        trial = self.load_trial(row["trial_record_id"])
        return AnnotationDocument.from_dict(trial, json.loads(row["document_json"]))

    def backup(self, target: Path) -> Path:
        target = target.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        os.close(descriptor)
        temp = Path(temp_name)
        try:
            backup_connection = sqlite3.connect(temp)
            try:
                self._connection.backup(backup_connection)
            finally:
                backup_connection.close()
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return target

    def _head(self, record_id: str) -> str | None:
        row = self._connection.execute(
            "SELECT revision_id FROM annotation_heads WHERE trial_record_id = ?",
            (record_id,),
        ).fetchone()
        return row[0] if row else None

    def _audit(self, action: str, entity_id: str, payload: dict, created_at: str | None = None) -> None:
        self._connection.execute(
            "INSERT INTO audit_log(action, entity_id, payload_json, created_at) VALUES (?, ?, ?, ?)",
            (action, entity_id, json.dumps(payload, ensure_ascii=False), created_at or _now()),
        )

    @staticmethod
    def _trial_from_row(row: sqlite3.Row) -> TrialRecord:
        return TrialRecord(
            id=row["id"],
            subject_id=row["subject_id"],
            session_id=row["session_id"],
            trial_id=row["trial_id"],
            source_path=Path(row["source_path"]),
            source_hash=row["source_hash"],
            input_file=row["input_file"],
            schema_id=row["schema_id"],
            schema_version=row["schema_version"],
        )

    @staticmethod
    def _trial_payload(record: TrialRecord) -> dict:
        payload = vars(record).copy()
        payload["source_path"] = str(record.source_path)
        return payload
