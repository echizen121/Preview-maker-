from __future__ import annotations

import json
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = {
    "background",
    "motion",
    "output",
    "width",
    "height",
    "fps",
    "duration",
    "model_x",
    "model_y",
    "model_scale",
}


class PresetError(Exception):
    pass


def resolve_project_path(root: Path, value: str) -> Path:
    if not value:
        raise PresetError("ファイルパスが未指定です。")
    path = Path(value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def relative_or_absolute(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def validate_preset(data: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(field for field in REQUIRED_FIELDS if data.get(field) in (None, ""))
    if missing:
        raise PresetError("必須項目が未指定です: " + ", ".join(missing))

    preset = dict(data)
    int_fields = ["width", "height", "fps", "duration", "model_x", "model_y"]
    float_fields = ["model_scale", "bgm_volume", "motion_start", "motion_end", "bgm_start", "bgm_end"]

    for field in int_fields:
        try:
            preset[field] = int(preset[field])
        except (TypeError, ValueError) as exc:
            raise PresetError(f"数値設定が不正です: {field}") from exc
        if preset[field] <= 0 and field not in {"model_x", "model_y"}:
            raise PresetError(f"数値設定は1以上にしてください: {field}")

    for field in float_fields:
        if field not in preset or preset[field] in (None, ""):
            continue
        try:
            preset[field] = float(preset[field])
        except (TypeError, ValueError) as exc:
            raise PresetError(f"数値設定が不正です: {field}") from exc

    if preset["model_scale"] <= 0:
        raise PresetError("モデル拡大率は0より大きい値にしてください。")
    if float(preset.get("bgm_volume", 1.0)) < 0:
        raise PresetError("BGM音量は0以上にしてください。")

    preset.setdefault("bgm", "")
    preset.setdefault("bgm_volume", 1.0)
    preset.setdefault("motion_start", 0.0)
    preset.setdefault("motion_end", "")
    preset.setdefault("bgm_start", 0.0)
    preset.setdefault("bgm_end", "")
    preset.setdefault("video_bitrate", "6000k")
    preset.setdefault("project_name", "")

    for start_field, end_field, label in [
        ("motion_start", "motion_end", "Live2D動画"),
        ("bgm_start", "bgm_end", "BGM"),
    ]:
        start = float(preset.get(start_field) or 0)
        if start < 0:
            raise PresetError(f"{label}開始秒は0以上にしてください。")
        end_value = preset.get(end_field)
        if end_value not in (None, "") and float(end_value) <= start:
            raise PresetError(f"{label}終了秒は開始秒より大きい値にしてください。")
    return preset


def load_preset(root: Path, path_value: str) -> dict[str, Any]:
    path = resolve_project_path(root, path_value)
    if not path.exists():
        raise PresetError(f"JSONファイルが見つかりません: {path_value}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PresetError(f"JSONの形式が不正です: {path_value}") from exc
    if not isinstance(data, dict):
        raise PresetError("JSONの形式が不正です: ルートはオブジェクトにしてください。")
    return validate_preset(data)


def save_preset(root: Path, path_value: str, preset: dict[str, Any]) -> str:
    path = resolve_project_path(root, path_value)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(preset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise PresetError(f"JSONの保存に失敗しました: {path_value}") from exc
    return relative_or_absolute(root, path)
