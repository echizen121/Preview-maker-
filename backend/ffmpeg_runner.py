from __future__ import annotations

import subprocess
from pathlib import Path

import imageio_ffmpeg


class FFmpegError(Exception):
    pass


def ffmpeg_executable() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str]) -> list[str]:
    command = [ffmpeg_executable(), *args]
    process = subprocess.run(command, capture_output=True, text=True)
    logs = []
    if process.stdout:
        logs.extend(process.stdout.splitlines())
    if process.stderr:
        logs.extend(process.stderr.splitlines())
    if process.returncode != 0:
        raise FFmpegError("\n".join(logs[-20:]) or "FFmpeg実行に失敗しました。")
    return logs


def assert_readable(path: Path, label: str) -> None:
    if not path.exists():
        raise FFmpegError(f"{label}が見つかりません: {path}")
    if not path.is_file():
        raise FFmpegError(f"{label}がファイルではありません: {path}")
