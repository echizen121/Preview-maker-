from __future__ import annotations

import hashlib
from pathlib import Path

from backend.ffmpeg_runner import FFmpegError, assert_readable, run_ffmpeg
from backend.preset import relative_or_absolute, resolve_project_path


class PreviewProxyError(Exception):
    pass


def preview_proxy_path(root: Path, source: Path) -> Path:
    stat = source.stat()
    key = f"{source.resolve()}:{stat.st_mtime_ns}:{stat.st_size}".encode("utf-8")
    digest = hashlib.sha1(key).hexdigest()[:16]
    return root / "projects" / "product_001" / "preview_cache" / f"{source.stem}_{digest}_vp8.webm"


def build_preview_proxy(root: Path, path_value: str) -> dict[str, str]:
    source = resolve_project_path(root, path_value)
    try:
        assert_readable(source, "プレビュー対象動画")
        output = preview_proxy_path(root, source)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists() and output.stat().st_mtime_ns >= source.stat().st_mtime_ns:
            return {"path": relative_or_absolute(root, output), "cached": "true"}

        run_ffmpeg(
            [
                "-y",
                "-i",
                str(source),
                "-an",
                "-vf",
                "scale=960:-2:flags=lanczos,fps=30",
                "-c:v",
                "libvpx",
                "-pix_fmt",
                "yuva420p",
                "-auto-alt-ref",
                "0",
                "-b:v",
                "1M",
                "-crf",
                "12",
                str(output),
            ]
        )
    except (OSError, FFmpegError) as exc:
        raise PreviewProxyError(f"プレビュー用WebMの作成に失敗しました: {path_value}\n{exc}") from exc
    return {"path": relative_or_absolute(root, output), "cached": "false"}
