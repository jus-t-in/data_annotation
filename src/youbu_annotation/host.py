"""Loopback-only HTTP host for the browser annotation interface."""

from __future__ import annotations

import argparse
import hmac
import ipaddress
import secrets
import socket
import threading
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .application import AnnotationApplication
from .project import (
    AnnotationProject,
    ProjectConflictError,
    ProjectError,
    ProjectSourceChangedError,
)


class TrialRegistration(BaseModel):
    source_path: str
    subject_id: str
    session_id: str
    trial_id: str


class WorkspaceOpen(BaseModel):
    trial_record_id: str
    recover_draft: bool = True


class CommandRequest(BaseModel):
    action: str
    payload: dict = Field(default_factory=dict)


class SchemaUpdate(BaseModel):
    expected_hash: str
    schema_value: dict


class ExpectedHash(BaseModel):
    expected_hash: str


def create_web_app(
    application: AnnotationApplication,
    *,
    public_origin: str,
    session_token: str | None = None,
    static_dir: Path | None = None,
) -> FastAPI:
    token = session_token or secrets.token_urlsafe(32)
    allowed_host = public_origin.removeprefix("http://")
    app = FastAPI(title="Youbu Annotation", docs_url=None, redoc_url=None)
    app.state.session_token = token

    @app.middleware("http")
    async def protect_local_host(request: Request, call_next):
        if request.headers.get("host") != allowed_host:
            return JSONResponse({"detail": "Host 不受允许"}, status_code=400)
        if request.url.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            supplied = request.headers.get("x-youbu-token", "")
            if request.headers.get("origin") != public_origin or not hmac.compare_digest(supplied, token):
                return JSONResponse({"detail": "本机会话校验失败"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.exception_handler(ProjectConflictError)
    async def project_conflict(_request: Request, exc: ProjectConflictError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ProjectSourceChangedError)
    async def source_changed(_request: Request, exc: ProjectSourceChangedError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ProjectError)
    async def project_error(_request: Request, exc: ProjectError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def invalid_command(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/bootstrap")
    async def bootstrap() -> dict:
        result = application.bootstrap()
        result["session_token"] = token
        return result

    @app.post("/api/trials")
    async def register_trial(value: TrialRegistration) -> dict:
        return application.register_trial(
            value.source_path,
            subject_id=value.subject_id,
            session_id=value.session_id,
            trial_id=value.trial_id,
        )

    @app.post("/api/workspaces")
    async def open_workspace(value: WorkspaceOpen) -> dict:
        return application.open_workspace(
            value.trial_record_id,
            recover_draft=value.recover_draft,
        )

    @app.get("/api/workspaces/{workspace_id}")
    async def workspace(workspace_id: str) -> dict:
        return application.workspace_view(workspace_id)

    @app.get("/api/workspaces/{workspace_id}/signals")
    async def signals(
        workspace_id: str,
        start_index: int = Query(0, ge=0),
        end_index: int | None = Query(None, ge=0),
        max_points: int = Query(12_000, ge=100, le=50_000),
    ) -> dict:
        return application.signals(
            workspace_id,
            start_index=start_index,
            end_index=end_index,
            max_points=max_points,
        )

    @app.post("/api/workspaces/{workspace_id}/commands")
    async def command(workspace_id: str, value: CommandRequest) -> dict:
        return application.command(workspace_id, value.action, value.payload)

    @app.post("/api/workspaces/{workspace_id}/recognize")
    async def run_recognizer(workspace_id: str) -> dict:
        return application.recognize(workspace_id)

    @app.post("/api/workspaces/{workspace_id}/commit")
    async def commit(workspace_id: str) -> dict:
        return application.commit(workspace_id)

    @app.delete("/api/workspaces/{workspace_id}", status_code=204)
    async def close_workspace(workspace_id: str) -> None:
        application.close_workspace(workspace_id)

    @app.put("/api/schema/draft")
    async def update_schema(value: SchemaUpdate) -> dict:
        return application.update_schema_draft(
            value.schema_value,
            expected_hash=value.expected_hash,
        )

    @app.post("/api/schema/publish")
    async def publish_schema(value: ExpectedHash) -> dict:
        return application.publish_schema_draft(expected_hash=value.expected_hash)

    static_dir = static_dir or Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if (static_dir / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<main><h1>Youbu Annotation</h1><p>前端尚未构建，请在 frontend 目录运行 npm run build。</p></main>",
            status_code=503,
        )

    return app


def _loopback_socket(host: str, port: int) -> socket.socket:
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ProjectError("服务地址必须是明确的 loopback IP") from exc
    if address.version != 4 or not address.is_loopback:
        raise ProjectError("服务只允许绑定 IPv4 loopback 地址")
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_socket.bind((host, port))
    server_socket.listen(128)
    return server_socket


def serve_project(
    project: AnnotationProject,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> None:
    server_socket = _loopback_socket(host, port)
    actual_port = server_socket.getsockname()[1]
    origin = f"http://{host}:{actual_port}"
    app = create_web_app(AnnotationApplication(project), public_origin=origin)
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(origin)).start()
    print(f"Youbu Annotation: {origin}")
    uvicorn.Server(
        uvicorn.Config(app, host=host, port=actual_port, log_level="info")
    ).run(sockets=[server_socket])


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="标注项目目录")
    parser.add_argument("--create", action="store_true", help="创建新项目")
    parser.add_argument("--name", default="步态标注项目", help="新项目名称")
    parser.add_argument("--port", type=int, default=0, help="本机端口，0 表示自动选择")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    project = (
        AnnotationProject.create(args.project, name=args.name)
        if args.create
        else AnnotationProject.open(args.project)
    )
    try:
        serve_project(project, port=args.port, open_browser=not args.no_browser)
    finally:
        project.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
