from __future__ import annotations

import csv
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from youbu_annotation import auto_annotate as legacy
from youbu_annotation.application import AnnotationApplication
from youbu_annotation.host import _loopback_socket, create_web_app
from youbu_annotation.project import AnnotationProject, ProjectError

ORIGIN = "http://127.0.0.1:8765"
TOKEN = "test-session-token"


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
        for index in range(6):
            row = {name: "0" for name in fields}
            row[legacy.ELAPSED_COLUMN] = str(index * 0.5)
            row[legacy.RIGHT_COLUMN] = str(index)
            writer.writerow(row)


@pytest.fixture
def hosted_project(tmp_path: Path):
    source = tmp_path / "source.csv"
    write_source(source)
    project = AnnotationProject.create(tmp_path / "project", name="浏览器标注")
    record = project.register_trial(source, subject_id="P01", session_id="S01", trial_id="T01")
    web_app = create_web_app(
        AnnotationApplication(project),
        public_origin=ORIGIN,
        session_token=TOKEN,
        static_dir=tmp_path / "missing-dist",
    )
    with TestClient(web_app, base_url=ORIGIN) as client:
        yield client, project, record
    project.close()


def mutation_headers() -> dict[str, str]:
    return {"origin": ORIGIN, "x-youbu-token": TOKEN}


def test_host_rejects_wrong_host_origin_and_token(hosted_project) -> None:
    client, _project, record = hosted_project
    assert client.get("/api/health", headers={"host": "attacker.invalid"}).status_code == 400
    assert client.post("/api/workspaces", json={"trial_record_id": record.id}).status_code == 403
    assert client.post(
        "/api/workspaces",
        json={"trial_record_id": record.id},
        headers={"origin": ORIGIN, "x-youbu-token": "wrong"},
    ).status_code == 403
    assert client.post(
        "/api/workspaces",
        json={"trial_record_id": record.id},
        headers=mutation_headers(),
    ).status_code == 200


def test_http_workspaces_commit_and_detect_stale_revision(hosted_project) -> None:
    client, _project, record = hosted_project
    headers = mutation_headers()
    workspaces = [
        client.post(
            "/api/workspaces",
            json={"trial_record_id": record.id},
            headers=headers,
        ).json()
        for _index in range(2)
    ]
    signals = client.get(f"/api/workspaces/{workspaces[0]['workspace_id']}/signals").json()
    assert signals["seconds"] == [0.0, 0.5, 1.0, 1.5, 2.0, 2.5]
    assert set(signals["channels"]) == {"left", "right", "pitch", "motion", "impact"}

    for workspace in workspaces:
        response = client.post(
            f"/api/workspaces/{workspace['workspace_id']}/commands",
            json={"action": "attest", "payload": {"annotator_id": "reviewer"}},
            headers=headers,
        )
        assert response.status_code == 200

    committed = client.post(
        f"/api/workspaces/{workspaces[0]['workspace_id']}/commit",
        headers=headers,
    )
    assert committed.status_code == 200
    assert committed.json()["committed_revision"]["annotator_id"] == "reviewer"
    conflict = client.post(
        f"/api/workspaces/{workspaces[1]['workspace_id']}/commit",
        headers=headers,
    )
    assert conflict.status_code == 409
    assert "重新加载" in conflict.json()["detail"]


def test_http_schema_draft_can_add_edit_deactivate_and_publish(hosted_project) -> None:
    client, project, _record = hosted_project
    headers = mutation_headers()
    draft = client.get("/api/bootstrap").json()["schema_draft"]
    original_hash = draft["content_hash"]
    draft["activity_labels"].append(
        {
            "code": "CUSTOM",
            "display_name": "实验动作",
            "color": "#D1495B",
            "description": "项目内手工标签",
            "active": True,
        }
    )
    draft["states"].append(
        {"activity": "CUSTOM", "terrain": "LEVEL", "color": "#D1495B"}
    )
    draft["activity_labels"][0]["display_name"] = "静止（已修改）"
    draft["activity_labels"][-2]["active"] = False

    updated_response = client.put(
        "/api/schema/draft",
        json={"expected_hash": original_hash, "schema_value": draft},
        headers=headers,
    )
    assert updated_response.status_code == 200
    updated = updated_response.json()
    assert updated["content_hash"] != original_hash
    published_response = client.post(
        "/api/schema/publish",
        json={"expected_hash": updated["content_hash"]},
        headers=headers,
    )
    assert published_response.status_code == 200
    published = published_response.json()
    assert published["version"] == 2
    assert project.schema_version(published["schema_id"], 1).content_hash == original_hash


def test_server_socket_refuses_non_loopback_address() -> None:
    with pytest.raises(ProjectError, match="loopback"):
        _loopback_socket("0.0.0.0", 0)
