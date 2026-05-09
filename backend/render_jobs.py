from __future__ import annotations

import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from backend.ffmpeg_runner import ffmpeg_executable
from backend.preset import relative_or_absolute
from backend.render import RenderError, build_render_args


MAX_LOG_LINES = 240


class RenderJobManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, preset: dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "logs": ["待機中"],
                "output": "",
                "error": "",
                "started_at": None,
                "finished_at": None,
                "process": None,
            }
        thread = threading.Thread(target=self._run, args=(job_id, preset), daemon=True)
        thread.start()
        return job_id

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            return {
                "id": job["id"],
                "status": job["status"],
                "logs": list(job["logs"]),
                "output": job["output"],
                "error": job["error"],
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
            }

    def cancel(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            process = job.get("process")
            if job["status"] in {"done", "error", "canceled"}:
                return True
            job["status"] = "canceled"
            self._append_log_locked(job, "キャンセル要求")
        if process and process.poll() is None:
            process.terminate()
        return True

    def _run(self, job_id: str, preset: dict[str, Any]) -> None:
        try:
            self._update(job_id, status="running", started_at=time.time(), logs=["素材確認中"])
            args, output = build_render_args(self.root, preset)
            self._append_log(job_id, "FFmpeg実行中")
            process = subprocess.Popen(
                [ffmpeg_executable(), *args],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            with self.lock:
                if job_id in self.jobs:
                    self.jobs[job_id]["process"] = process
            assert process.stdout is not None
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    self._append_log(job_id, line)
            return_code = process.wait()
            current = self.status(job_id)
            if current and current["status"] == "canceled":
                self._update(job_id, finished_at=time.time(), process=None)
                return
            if return_code != 0:
                raise RenderError("FFmpegによる動画出力に失敗しました。入力動画の形式を確認してください。")
            self._update(
                job_id,
                status="done",
                output=relative_or_absolute(self.root, output),
                finished_at=time.time(),
                process=None,
            )
            self._append_log(job_id, "完了")
        except Exception as exc:  # noqa: BLE001 - UIへ失敗理由を返す境界
            self._update(job_id, status="error", error=str(exc), finished_at=time.time(), process=None)
            self._append_log(job_id, f"エラー: {exc}")

    def _update(self, job_id: str, **updates: Any) -> None:
        logs = updates.pop("logs", None)
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            if logs:
                for line in logs:
                    self._append_log_locked(job, line)

    def _append_log(self, job_id: str, line: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job:
                self._append_log_locked(job, line)

    def _append_log_locked(self, job: dict[str, Any], line: str) -> None:
        job["logs"].append(line)
        if len(job["logs"]) > MAX_LOG_LINES:
            job["logs"] = job["logs"][-MAX_LOG_LINES:]
