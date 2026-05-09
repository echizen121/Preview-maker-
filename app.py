from __future__ import annotations

import socket
import threading
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.asset_library import asset_library, ensure_directories, rename_asset, save_upload
from backend.preset import PresetError, load_preset, save_preset, validate_preset
from backend.preview_proxy import PreviewProxyError, build_preview_proxy
from backend.render import RenderError, render_video
from backend.render_jobs import RenderJobManager


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
HOST = "127.0.0.1"
PORT = 7860

@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_directories(ROOT)
    yield


app = FastAPI(title="Live2D Booth Preview Maker", lifespan=lifespan)
render_jobs = RenderJobManager(ROOT)


class PresetPayload(BaseModel):
    path: str
    preset: dict[str, Any]


class RenderPayload(BaseModel):
    preset: dict[str, Any]


class AssetPathPayload(BaseModel):
    path: str


class RenameAssetPayload(BaseModel):
    path: str
    new_name: str


@app.get("/api/assets")
def api_assets() -> dict[str, Any]:
    return asset_library(ROOT)


@app.get("/asset/{asset_path:path}")
def api_asset(asset_path: str) -> FileResponse:
    path = (ROOT / asset_path).resolve()
    allowed_roots = [(ROOT / "templates").resolve(), (ROOT / "projects").resolve()]
    if not any(path == base or base in path.parents for base in allowed_roots):
        raise HTTPException(status_code=403, detail="許可されていない素材パスです。")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"素材が見つかりません: {asset_path}")
    return FileResponse(path)


@app.post("/api/upload/{kind}")
async def api_upload(kind: str, file: UploadFile, save_name: str = Form("")) -> dict[str, str]:
    try:
        path = save_upload(ROOT, kind, file.filename or "upload.bin", await file.read(), save_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path}


@app.post("/api/asset/delete")
def api_delete_asset(payload: AssetPathPayload) -> dict[str, str]:
    path = (ROOT / payload.path).resolve()
    allowed_roots = [
        (ROOT / "templates" / "background").resolve(),
        (ROOT / "templates" / "bgm").resolve(),
        (ROOT / "projects").resolve(),
    ]
    if not any(path == base or base in path.parents for base in allowed_roots):
        raise HTTPException(status_code=403, detail="許可されていない削除パスです。")
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"ファイルが見つかりません: {payload.path}")
    try:
        path.unlink()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"ファイルを削除できません: {payload.path}") from exc
    return {"path": payload.path}


@app.post("/api/asset/rename")
def api_rename_asset(payload: RenameAssetPayload) -> dict[str, str]:
    try:
        new_path = rename_asset(ROOT, payload.path, payload.new_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"old_path": payload.path, "path": new_path}


@app.post("/api/preview-proxy")
def api_preview_proxy(payload: AssetPathPayload) -> dict[str, str]:
    try:
        return build_preview_proxy(ROOT, payload.path)
    except (PresetError, PreviewProxyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/preset/load")
def api_load_preset(payload: dict[str, str]) -> dict[str, Any]:
    try:
        return {"preset": load_preset(ROOT, payload.get("path", ""))}
    except PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/preset/save")
def api_save_preset(payload: PresetPayload) -> dict[str, str]:
    try:
        preset = validate_preset(payload.preset)
        path = save_preset(ROOT, payload.path, preset)
    except PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": path}


@app.post("/api/render")
def api_render(payload: RenderPayload) -> dict[str, Any]:
    try:
        preset = validate_preset(payload.preset)
        return render_video(ROOT, preset)
    except (PresetError, RenderError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/render/start")
def api_render_start(payload: RenderPayload) -> dict[str, str]:
    try:
        preset = validate_preset(payload.preset)
        job_id = render_jobs.start(preset)
    except PresetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"job_id": job_id}


@app.get("/api/render/status/{job_id}")
def api_render_status(job_id: str) -> dict[str, Any]:
    status = render_jobs.status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="レンダージョブが見つかりません。")
    return status


@app.post("/api/render/cancel/{job_id}")
def api_render_cancel(job_id: str) -> dict[str, str]:
    if not render_jobs.cancel(job_id):
        raise HTTPException(status_code=404, detail="レンダージョブが見つかりません。")
    return {"job_id": job_id, "status": "canceled"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


def server_is_running(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, port)) == 0


def open_browser_later(url: str) -> None:
    def worker() -> None:
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=worker, daemon=True).start()


if __name__ == "__main__":
    import sys

    import uvicorn

    url = f"http://{HOST}:{PORT}"
    open_browser = "--no-browser" not in sys.argv
    if server_is_running(HOST, PORT):
        if open_browser:
            webbrowser.open(url)
    else:
        if open_browser:
            open_browser_later(url)
        uvicorn.run(app, host=HOST, port=PORT, log_level="info")
