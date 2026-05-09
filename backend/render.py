from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.ffmpeg_runner import FFmpegError, assert_readable, run_ffmpeg
from backend.preset import relative_or_absolute, resolve_project_path


class RenderError(Exception):
    pass


def overlay_expression(axis: str, center_value: int) -> str:
    if axis == "x":
        return f"{center_value}-(overlay_w/2)"
    return f"{center_value}-(overlay_h/2)"


def source_range(preset: dict[str, Any], prefix: str, duration: int) -> tuple[float, float, float]:
    start = float(preset.get(f"{prefix}_start") or 0)
    end_value = preset.get(f"{prefix}_end")
    end = float(end_value) if end_value not in (None, "") else start + duration
    length = max(0.1, end - start)
    return start, end, length


def build_render_args(root: Path, preset: dict[str, Any]) -> tuple[list[str], Path]:
    background = resolve_project_path(root, str(preset["background"]))
    motion = resolve_project_path(root, str(preset["motion"]))
    output = resolve_project_path(root, str(preset["output"]))
    bgm_value = str(preset.get("bgm", "") or "")
    bgm = resolve_project_path(root, bgm_value) if bgm_value else None

    try:
        assert_readable(background, "背景画像")
        assert_readable(motion, "Live2D動画")
        if bgm:
            assert_readable(bgm, "BGM")
        output.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, FFmpegError) as exc:
        raise RenderError(str(exc)) from exc

    width = int(preset["width"])
    height = int(preset["height"])
    fps = int(preset["fps"])
    duration = int(preset["duration"])
    scale = float(preset["model_scale"])
    volume = float(preset.get("bgm_volume", 1.0))
    bitrate = str(preset.get("video_bitrate", "6000k") or "6000k")
    motion_start, motion_end, motion_length = source_range(preset, "motion", duration)
    bgm_start, bgm_end, bgm_length = source_range(preset, "bgm", duration)
    motion_loop_frames = max(1, int(round(motion_length * fps)))
    bgm_sample_rate = 48000
    bgm_loop_samples = max(1, int(round(bgm_length * bgm_sample_rate)))
    x_expr = overlay_expression("x", int(preset["model_x"]))
    y_expr = overlay_expression("y", int(preset["model_y"]))

    args = [
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        str(duration),
        "-i",
        str(background),
        "-i",
        str(motion),
    ]

    if bgm:
        args.extend(["-i", str(bgm)])

    video_filter = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[bg];"
        f"[1:v]trim=start={motion_start}:end={motion_end},setpts=PTS-STARTPTS,"
        f"loop=loop=-1:size={motion_loop_frames}:start=0,"
        f"trim=duration={duration},fps={fps},scale=iw*{scale}:ih*{scale}[model];"
        f"[bg][model]overlay={x_expr}:{y_expr}:format=auto,format=yuv420p[v]"
    )

    if bgm:
        audio_filter = (
            f";[2:a]aresample={bgm_sample_rate},atrim=start={bgm_start}:end={bgm_end},"
            f"asetpts=PTS-STARTPTS,aloop=loop=-1:size={bgm_loop_samples}:start=0,"
            f"atrim=duration={duration},volume={volume}[a]"
        )
        args.extend(["-filter_complex", video_filter + audio_filter, "-map", "[v]", "-map", "[a]"])
    else:
        args.extend(["-filter_complex", video_filter, "-map", "[v]", "-an"])

    args.extend(
        [
            "-t",
            str(duration),
            "-r",
            str(fps),
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
        ]
    )
    if bgm:
        args.extend(["-c:a", "aac", "-shortest"])
    if output.suffix.lower() in {".mp4", ".m4v", ".mov"}:
        args.extend(["-movflags", "+faststart"])
    args.append(str(output))
    return args, output


def render_video(root: Path, preset: dict[str, Any]) -> dict[str, Any]:
    args, output = build_render_args(root, preset)
    try:
        logs = ["素材確認中", "FFmpeg実行中", *run_ffmpeg(args), "完了"]
    except FFmpegError as exc:
        raise RenderError("FFmpegによる動画出力に失敗しました。入力動画の形式を確認してください。\n" + str(exc)) from exc

    return {"output": relative_or_absolute(root, output), "logs": logs[-40:]}
