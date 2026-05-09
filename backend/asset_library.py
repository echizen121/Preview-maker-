from __future__ import annotations

from pathlib import Path

from backend.preset import relative_or_absolute


BACKGROUND_EXTS = {".png", ".jpg", ".jpeg"}
MOTION_EXTS = {".webm", ".mov", ".mp4", ".mkv"}
BGM_EXTS = {".mp3", ".wav", ".m4a"}
PRESET_EXTS = {".json"}
OUTPUT_EXTS = {".mp4", ".mkv"}


DIRECTORIES = [
    "templates/background",
    "templates/bgm",
    "templates/presets",
    "projects/product_001/output",
    "projects/product_001/preview_cache",
    "resources",
]


def ensure_directories(root: Path) -> None:
    for directory in DIRECTORIES:
        (root / directory).mkdir(parents=True, exist_ok=True)


def collect_files(root: Path, bases: list[str], extensions: set[str]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for base in bases:
        directory = root / base
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*")):
            if path.is_file() and path.suffix.lower() in extensions:
                files.append({"name": path.name, "path": relative_or_absolute(root, path)})
    return files


def collect_motion_files(root: Path) -> list[dict[str, str]]:
    return [
        item
        for item in collect_files(root, ["projects"], MOTION_EXTS)
        if "output" not in Path(item["path"]).parts and "preview_cache" not in Path(item["path"]).parts
    ]


def collect_output_files(root: Path) -> list[dict[str, str]]:
    return [
        item
        for item in collect_files(root, ["projects"], OUTPUT_EXTS)
        if "output" in Path(item["path"]).parts
    ]


def asset_library(root: Path) -> dict[str, list[dict[str, str]]]:
    ensure_directories(root)
    return {
        "backgrounds": collect_files(root, ["templates/background"], BACKGROUND_EXTS),
        "motions": collect_motion_files(root),
        "bgms": collect_files(root, ["templates/bgm"], BGM_EXTS),
        "presets": collect_files(root, ["templates/presets", "projects"], PRESET_EXTS),
        "outputs": collect_output_files(root),
    }


INVALID_FILENAME_CHARS = set('<>:"/\\|?*')


def safe_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    cleaned = "".join("_" if char in INVALID_FILENAME_CHARS or ord(char) < 32 else char for char in name)
    cleaned = cleaned.strip(" .")
    return cleaned or "upload.bin"


def library_filename(filename: str, save_name: str) -> str:
    original = safe_filename(filename)
    original_suffix = Path(original).suffix
    if not save_name.strip():
        return original
    saved = safe_filename(save_name.strip())
    if not Path(saved).suffix and original_suffix:
        saved += original_suffix
    return saved


def save_upload(root: Path, kind: str, filename: str, content: bytes, save_name: str = "") -> str:
    targets = {
        "background": ("templates/background", BACKGROUND_EXTS),
        "motion": ("projects/product_001", MOTION_EXTS),
        "bgm": ("templates/bgm", BGM_EXTS),
    }
    if kind not in targets:
        raise ValueError("不明な素材種別です。")
    directory_name, extensions = targets[kind]
    safe_name = library_filename(filename, save_name)
    if Path(safe_name).suffix.lower() not in extensions:
        raise ValueError("対応していないファイル形式です。")
    directory = root / directory_name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / safe_name
    path.write_bytes(content)
    return relative_or_absolute(root, path)


def rename_asset(root: Path, path_value: str, new_name: str) -> str:
    source = (root / path_value).resolve()
    allowed_roots = [
        (root / "templates" / "background").resolve(),
        (root / "templates" / "bgm").resolve(),
        (root / "projects").resolve(),
    ]
    if not any(source == base or base in source.parents for base in allowed_roots):
        raise ValueError("許可されていない素材パスです。")
    if not source.is_file():
        raise ValueError(f"素材が見つかりません: {path_value}")

    target_name = library_filename(source.name, new_name)
    if Path(target_name).suffix.lower() != source.suffix.lower():
        raise ValueError("拡張子は変更できません。")
    target = source.with_name(target_name)
    if target == source:
        return relative_or_absolute(root, source)
    if target.exists():
        raise ValueError(f"同名のファイルが既に存在します: {target_name}")
    source.rename(target)
    return relative_or_absolute(root, target)
