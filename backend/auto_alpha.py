from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from backend.asset_library import MOTION_EXTS, library_filename, safe_filename
from backend.ffmpeg_runner import ffmpeg_executable
from backend.preset import relative_or_absolute


class AutoAlphaError(Exception):
    pass


ALPHA_DIR = "projects/product_001/alpha_cache"
MAX_UPLOAD_SIZE = 2 * 1024 * 1024 * 1024
EDGE_RATIO = 0.05
COLOR_BIN = 10
MAX_LOG_LINES = 120
SOFT_EDGE_RADIUS = 1
CONNECTIVITY = 4
FOREGROUND_PROTECT_RADIUS = 14
BACKGROUND_ERODE_RADIUS = 2
MAX_BACKGROUND_MASK_AREA = 0.76
MIN_CENTER_KEEP_RATIO = 0.88
MAX_MASK_AREA_JUMP = 0.12
AlphaLogger = Callable[[str], None]


class AutoAlphaJobManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def start(self, filename: str, content: bytes, mode: str) -> str:
        job_id = uuid.uuid4().hex
        with self.lock:
            self.jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "logs": ["待機中"],
                "result": {},
                "error": "",
                "started_at": None,
                "finished_at": None,
            }
        thread = threading.Thread(target=self._run, args=(job_id, filename, content, mode), daemon=True)
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
                "result": dict(job["result"]),
                "error": job["error"],
                "started_at": job["started_at"],
                "finished_at": job["finished_at"],
            }

    def _run(self, job_id: str, filename: str, content: bytes, mode: str) -> None:
        self._update(job_id, status="running", started_at=time.time(), logs=["アップロード受信完了"])
        try:
            result = process_video_asset(self.root, filename, content, mode, lambda line: self._append_log(job_id, line))
            self._update(job_id, status="done", result=result, finished_at=time.time())
            self._append_log(job_id, "完了")
        except Exception as exc:  # noqa: BLE001 - UIへ失敗理由を返す境界
            self._update(job_id, status="error", error=str(exc), finished_at=time.time())
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


def process_video_asset(root: Path, filename: str, content: bytes, mode: str, logger: AlphaLogger | None = None) -> dict[str, object]:
    log = logger or (lambda _: None)
    if mode not in {"auto_alpha", "as_is"}:
        raise AutoAlphaError("不明な処理方式です。")
    if len(content) > MAX_UPLOAD_SIZE:
        raise AutoAlphaError("ファイルサイズが大きすぎます。")

    original_name = safe_filename(filename or "motion.mkv")
    suffix = Path(original_name).suffix.lower()
    if suffix not in MOTION_EXTS:
        raise AutoAlphaError("対応していない動画形式です。")

    digest = hashlib.sha256(content).hexdigest()
    cache_dir = root / ALPHA_DIR
    source_dir = cache_dir / "source"
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_dir.mkdir(parents=True, exist_ok=True)

    log("入力ファイルをキャッシュへ保存中")
    source = source_dir / f"{Path(original_name).stem}_{digest[:12]}{suffix}"
    if not source.exists() or source.read_bytes() != content:
        source.write_bytes(content)

    if mode == "as_is":
        log("透過せず素材として登録中")
        return register_original(root, source, original_name, digest)
    return register_alpha(root, source, original_name, digest, log)


def register_original(root: Path, source: Path, original_name: str, digest: str) -> dict[str, object]:
    target = root / ALPHA_DIR / library_filename(original_name, f"{Path(original_name).stem}_original{source.suffix}")
    target = unique_for_digest(target, digest)
    metadata = target.with_suffix(target.suffix + ".json")
    cached = target.exists()
    if not target.exists():
        shutil.copy2(source, target)
    write_metadata(
        root,
        metadata,
        {
            "source_name": original_name,
            "source_sha256": digest,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "as_is",
            "output_format": target.suffix.lower().lstrip("."),
            "output_path": relative_or_absolute(root, target),
        },
    )
    return {
        "path": relative_or_absolute(root, target),
        "mode": "as_is",
        "logs": ["そのまま素材として登録完了"],
        "cached": cached,
    }


def register_alpha(root: Path, source: Path, original_name: str, digest: str, log: AlphaLogger) -> dict[str, object]:
    target = root / ALPHA_DIR / f"{Path(original_name).stem}_{digest[:12]}_alpha_temporal_v4.mov"
    metadata = target.with_suffix(".mov.json")
    if target.exists() and metadata.exists():
        cached_data = read_metadata(metadata)
        log("既存キャッシュを使用します")
        return {
            "path": relative_or_absolute(root, target),
            "mode": "auto_alpha",
            "estimated_color": cached_data.get("estimated_background_color", ""),
            "confidence": cached_data.get("confidence", ""),
            "logs": ["既存キャッシュを使用しました", "素材化完了"],
            "cached": True,
        }

    cv2, np = require_cv()
    timings: dict[str, float] = {}
    total_started = time.perf_counter()

    log("動画情報を解析中")
    info_started = time.perf_counter()
    video_info = probe_video_info_cv(source)
    duration = video_info["duration"]
    timings["video_probe_seconds"] = elapsed(info_started)
    sample_times = sample_frame_times(duration, digest)
    log(f"サンプルフレーム: {', '.join(str(value) for value in sample_times)} 秒")

    log("外縁色を集計中")
    estimate_started = time.perf_counter()
    sample_frames = read_sample_frames_cv(source, sample_times)
    if not sample_frames:
        raise AutoAlphaError("背景色を推定できませんでした。")
    bg_lab, bg_rgb, confidence = estimate_background_lab(sample_frames)
    timings["background_estimation_seconds"] = elapsed(estimate_started)
    if confidence < 0.15:
        raise AutoAlphaError("単色背景としての信頼度が低いため、透過処理を中止しました。")

    threshold_started = time.perf_counter()
    threshold, threshold_report = choose_threshold(sample_frames, bg_lab)
    timings["threshold_evaluation_seconds"] = elapsed(threshold_started)
    color_hex = "#{:02x}{:02x}{:02x}".format(*bg_rgb)
    log(f"推定背景色: {color_hex}")
    log(f"背景色信頼度: {confidence:.3f}")
    log(f"背景判定しきい値: {threshold}")
    log(f"前景保護半径: {FOREGROUND_PROTECT_RADIUS}px")
    log(f"背景マスク収縮: {BACKGROUND_ERODE_RADIUS}px")
    log(f"時間方向補正: 中央保持率 {MIN_CENTER_KEEP_RATIO:.2f} 未満または面積急増時に直前マスクを使用")
    log(
        "評価: "
        f"外縁透明化率 {threshold_report['edge_ratio']:.3f}, "
        f"中央不透明保持率 {threshold_report['center_keep_ratio']:.3f}, "
        f"平均マスク面積 {threshold_report['mask_area_mean']:.3f}"
    )
    log("透過動画を生成中")
    render_result = build_alpha_video_cv(source, target, bg_lab, threshold, log)
    timings.update(render_result["timings"])
    timings["total_seconds"] = elapsed(total_started)
    log("透過済み素材を登録中")

    write_metadata(
        root,
        metadata,
        {
            "source_name": original_name,
            "source_sha256": digest,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": "auto_alpha",
            "estimated_background_color": color_hex,
            "confidence": round(confidence, 4),
            "threshold": threshold,
            "threshold_report": threshold_report,
            "foreground_protect_radius": FOREGROUND_PROTECT_RADIUS,
            "background_erode_radius": BACKGROUND_ERODE_RADIUS,
            "max_background_mask_area": MAX_BACKGROUND_MASK_AREA,
            "min_center_keep_ratio": MIN_CENTER_KEEP_RATIO,
            "max_mask_area_jump": MAX_MASK_AREA_JUMP,
            "connectivity": CONNECTIVITY,
            "sample_times": sample_times,
            "output_format": "mov_prores_4444",
            "output_path": relative_or_absolute(root, target),
            "input_width": video_info["width"],
            "input_height": video_info["height"],
            "input_fps": video_info["fps"],
            "input_duration": video_info["duration"],
            "input_frame_count": video_info["frame_count"],
            "output_frame_count": render_result["output_frame_count"],
            "warnings": render_result["warnings"],
            "timings": timings,
        },
    )
    for key, value in timings.items():
        log(f"時間: {key}={value:.2f}s")
    if render_result["warnings"]:
        log("警告: " + " / ".join(render_result["warnings"]))
    return {
        "path": relative_or_absolute(root, target),
        "mode": "auto_alpha",
        "estimated_color": color_hex,
        "confidence": confidence,
        "threshold": threshold,
        "warnings": render_result["warnings"],
        "logs": ["解析中", f"推定背景色: {color_hex}", "透過処理中", "素材化完了"],
        "cached": False,
    }


def unique_for_digest(path: Path, digest: str) -> Path:
    return path.with_name(f"{path.stem}_{digest[:12]}{path.suffix}")


def write_metadata(root: Path, path: Path, data: dict[str, object]) -> None:
    data["metadata_path"] = relative_or_absolute(root, path)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_metadata(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def probe_duration(path: Path) -> float:
    command = [ffmpeg_executable(), "-hide_banner", "-i", str(path)]
    process = subprocess.run(command, capture_output=True, text=True)
    text = process.stderr + process.stdout
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        raise AutoAlphaError("動画ファイルとして扱えません。")
    hours, minutes, seconds = match.groups()
    duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    if duration <= 0:
        raise AutoAlphaError("動画の長さを取得できませんでした。")
    return duration


def sample_frame_times(duration: float, digest: str) -> list[float]:
    if duration <= 2:
        count = 3
    elif duration <= 8:
        count = 5
    elif duration <= 30:
        count = 8
    else:
        count = 12
    count = max(1, min(count, int(max(1, duration * 2))))
    rng = random.Random(digest)
    margin = min(0.2, duration / 10)
    usable_start = margin
    usable_end = max(usable_start, duration - margin)
    segment = max(0.01, (usable_end - usable_start) / count)
    times = []
    for index in range(count):
        start = usable_start + segment * index
        end = min(usable_end, start + segment)
        times.append(round(rng.uniform(start, end), 3))
    return times


def require_cv() -> tuple[Any, Any]:
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
    except ImportError as exc:
        raise AutoAlphaError("OpenCV / NumPy が未インストールです。setup_windows.bat を再実行してください。") from exc
    return cv2, np


def elapsed(started_at: float) -> float:
    return time.perf_counter() - started_at


def open_capture(path: Path):
    cv2, _ = require_cv()
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise AutoAlphaError(f"動画ファイルとして扱えません: {path}")
    return capture


def probe_video_info_cv(path: Path) -> dict[str, float | int]:
    cv2, _ = require_cv()
    capture = open_capture(path)
    try:
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    finally:
        capture.release()
    if width <= 0 or height <= 0:
        raise AutoAlphaError("動画サイズを取得できませんでした。")
    duration = frame_count / fps if frame_count > 0 and fps > 0 else probe_duration(path)
    return {
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": frame_count,
        "duration": duration,
    }


def read_sample_frames_cv(path: Path, sample_times: list[float]) -> list[Any]:
    cv2, _ = require_cv()
    capture = open_capture(path)
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    frames = []
    try:
        for timestamp in sample_times:
            capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(timestamp * fps)))
            ok, frame = capture.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        capture.release()
    return frames


def estimate_background_lab(frames: list[Any]) -> tuple[Any, tuple[int, int, int], float]:
    cv2, np = require_cv()
    lab_edges = []
    for frame in frames:
        edge_pixels = edge_pixels_from_frame(frame)
        if len(edge_pixels) == 0:
            continue
        if len(edge_pixels) > 12000:
            step = max(1, len(edge_pixels) // 12000)
            edge_pixels = edge_pixels[::step]
        lab = cv2.cvtColor(edge_pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB).reshape(-1, 3)
        lab_edges.append(lab)
    if not lab_edges:
        raise AutoAlphaError("背景色を推定できませんでした。")

    samples = np.concatenate(lab_edges, axis=0).astype(np.int16)
    bins = samples // COLOR_BIN
    unique_bins, inverse, counts = np.unique(bins, axis=0, return_inverse=True, return_counts=True)
    best_index = int(np.argmax(counts))
    selected = samples[inverse == best_index]
    representative_lab = np.median(selected, axis=0).astype(np.uint8)
    rgb = cv2.cvtColor(representative_lab.reshape(1, 1, 3), cv2.COLOR_LAB2RGB).reshape(3)
    confidence = float(counts[best_index] / max(1, counts.sum()))
    return representative_lab.astype(np.int16), tuple(int(value) for value in rgb), confidence


def edge_pixels_from_frame(frame: Any) -> Any:
    _, np = require_cv()
    height, width = frame.shape[:2]
    band = max(2, int(min(width, height) * EDGE_RATIO))
    top = frame[:band, :, :]
    bottom = frame[height - band :, :, :]
    left = frame[band : height - band, :band, :]
    right = frame[band : height - band, width - band :, :]
    return np.concatenate(
        [
            top.reshape(-1, 3),
            bottom.reshape(-1, 3),
            left.reshape(-1, 3),
            right.reshape(-1, 3),
        ],
        axis=0,
    )


def choose_threshold(frames: list[Any], bg_lab: Any) -> tuple[int, dict[str, float]]:
    candidates = [2, 3, 4, 5, 6, 8, 10, 12, 14, 16]
    best_threshold = candidates[0]
    best_report: dict[str, float] = {}
    best_score = float("inf")
    for threshold in candidates:
        reports = [evaluate_frame_threshold(frame, bg_lab, threshold) for frame in frames]
        edge_ratio = mean(report["edge_ratio"] for report in reports)
        center_keep = mean(report["center_keep_ratio"] for report in reports)
        mask_area = mean(report["mask_area"] for report in reports)
        mask_variance = variance(report["mask_area"] for report in reports)
        score = 0.0
        score += max(0.0, 0.55 - edge_ratio) * 2.0
        score += max(0.0, 0.96 - center_keep) * 60.0
        score += max(0.0, mask_area - 0.58) * 32.0
        score += mask_variance * 8.0
        score += threshold * 0.03
        if center_keep < 0.88:
            score += 100.0
        if mask_area > 0.72:
            score += 50.0
        if score < best_score:
            best_score = score
            best_threshold = threshold
            best_report = {
                "edge_ratio": edge_ratio,
                "center_keep_ratio": center_keep,
                "mask_area_mean": mask_area,
                "mask_area_variance": mask_variance,
                "score": score,
            }
    return best_threshold, best_report


def evaluate_frame_threshold(frame: Any, bg_lab: Any, threshold: int) -> dict[str, float]:
    bg_mask = connected_background_mask(frame, bg_lab, threshold)
    edge = edge_mask_for_shape(bg_mask.shape)
    center = center_mask_for_shape(bg_mask.shape)
    return {
        "edge_ratio": float(bg_mask[edge].mean()) if bg_mask[edge].size else 0.0,
        "center_keep_ratio": float((~bg_mask[center]).mean()) if bg_mask[center].size else 1.0,
        "mask_area": float(bg_mask.mean()),
    }


def connected_background_mask(frame: Any, bg_lab: Any, threshold: int) -> Any:
    cv2, np = require_cv()
    candidate = background_candidate_mask(frame, bg_lab, threshold).astype(np.uint8)
    if FOREGROUND_PROTECT_RADIUS > 0:
        protected = protected_foreground_mask(candidate)
        candidate = np.where(protected, 0, candidate).astype(np.uint8)

    label_count, labels = cv2.connectedComponents(candidate, connectivity=CONNECTIVITY)
    if label_count <= 1:
        return np.zeros(candidate.shape, dtype=bool)
    edge_labels = np.unique(
        np.concatenate(
            [
                labels[0, :],
                labels[-1, :],
                labels[:, 0],
                labels[:, -1],
            ]
        )
    )
    edge_labels = edge_labels[edge_labels != 0]
    if edge_labels.size == 0:
        return np.zeros(candidate.shape, dtype=bool)

    return np.isin(labels, edge_labels)


def protected_foreground_mask(candidate: Any) -> Any:
    cv2, np = require_cv()
    foreground_core = candidate == 0
    size = FOREGROUND_PROTECT_RADIUS * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    protected = cv2.dilate(foreground_core.astype(np.uint8), kernel, iterations=1).astype(bool)

    # 前景で囲まれた背景色領域は、外縁との細いリークで抜けやすい。
    # 保守優先で、保護領域に囲まれた穴も前景側として残す。
    open_area = (~protected).astype(np.uint8)
    label_count, labels = cv2.connectedComponents(open_area, connectivity=CONNECTIVITY)
    if label_count <= 1:
        return protected
    edge_labels = np.unique(
        np.concatenate(
            [
                labels[0, :],
                labels[-1, :],
                labels[:, 0],
                labels[:, -1],
            ]
        )
    )
    edge_labels = edge_labels[edge_labels != 0]
    edge_reachable = np.isin(labels, edge_labels)
    enclosed = (open_area.astype(bool)) & ~edge_reachable
    return protected | enclosed


def background_candidate_mask(frame: Any, bg_lab: Any, threshold: int) -> Any:
    cv2, np = require_cv()
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.int16)
    delta = lab - bg_lab.reshape(1, 1, 3)
    return np.sum(delta * delta, axis=2) <= threshold * threshold


def refined_background_mask(frame: Any, bg_lab: Any, threshold: int) -> Any:
    cv2, np = require_cv()
    bg_mask = connected_background_mask(frame, bg_lab, threshold)
    mask_u8 = bg_mask.astype(np.uint8) * 255
    if BACKGROUND_ERODE_RADIUS > 0:
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask_u8 = cv2.erode(mask_u8, kernel, iterations=BACKGROUND_ERODE_RADIUS)
    return mask_u8 > 0


def alpha_from_background_mask(bg_mask: Any) -> Any:
    cv2, np = require_cv()
    alpha = np.where(bg_mask, 0, 255).astype(np.uint8)
    if SOFT_EDGE_RADIUS <= 0:
        return alpha
    kernel_size = SOFT_EDGE_RADIUS * 2 + 1
    return cv2.GaussianBlur(alpha, (kernel_size, kernel_size), 0)


def rgba_from_frame(frame: Any, bg_lab: Any, threshold: int) -> tuple[Any, float]:
    cv2, np = require_cv()
    bg_mask = refined_background_mask(frame, bg_lab, threshold)
    return rgba_from_mask(frame, bg_mask), float(bg_mask.mean())


def rgba_from_mask(frame: Any, bg_mask: Any) -> Any:
    cv2, np = require_cv()
    alpha = alpha_from_background_mask(bg_mask)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return np.dstack([rgb, alpha])


def mask_stats(bg_mask: Any) -> dict[str, float]:
    center = center_mask_for_shape(bg_mask.shape)
    return {
        "center_keep_ratio": float((~bg_mask[center]).mean()) if bg_mask[center].size else 1.0,
        "mask_area": float(bg_mask.mean()),
    }


def mask_needs_temporal_fallback(bg_mask: Any, previous_area: float | None) -> str:
    stats = mask_stats(bg_mask)
    if stats["center_keep_ratio"] < MIN_CENTER_KEEP_RATIO:
        return f"中央保持率低下 {stats['center_keep_ratio']:.3f}"
    if stats["mask_area"] > MAX_BACKGROUND_MASK_AREA:
        return f"マスク面積過大 {stats['mask_area']:.3f}"
    if previous_area is not None and stats["mask_area"] - previous_area > MAX_MASK_AREA_JUMP:
        return f"マスク面積急増 {previous_area:.3f}->{stats['mask_area']:.3f}"
    return ""


def edge_mask_for_shape(shape: tuple[int, int]) -> Any:
    _, np = require_cv()
    height, width = shape
    band = max(2, int(min(width, height) * EDGE_RATIO))
    mask = np.zeros(shape, dtype=bool)
    mask[:band, :] = True
    mask[height - band :, :] = True
    mask[:, :band] = True
    mask[:, width - band :] = True
    return mask


def center_mask_for_shape(shape: tuple[int, int]) -> Any:
    _, np = require_cv()
    height, width = shape
    mask = np.zeros(shape, dtype=bool)
    y0, y1 = int(height * 0.25), int(height * 0.75)
    x0, x1 = int(width * 0.25), int(width * 0.75)
    mask[y0:y1, x0:x1] = True
    return mask


def mean(values: Any) -> float:
    items = list(values)
    return sum(items) / max(1, len(items))


def variance(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    avg = mean(items)
    return mean([(value - avg) ** 2 for value in items])


def build_alpha_video_cv(source: Path, target: Path, bg_lab: Any, threshold: int, log: AlphaLogger) -> dict[str, Any]:
    cv2, _ = require_cv()
    capture = open_capture(source)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    input_frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    log(f"フレーム処理: {width}x{height} / {fps:g}fps")

    encoder = subprocess.Popen(
        [
            ffmpeg_executable(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{width}x{height}",
            "-r",
            f"{fps:g}",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4",
            "-pix_fmt",
            "yuva444p10le",
            str(target),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert encoder.stdin is not None

    timings = {
        "frame_read_seconds": 0.0,
        "mask_generation_seconds": 0.0,
        "rgba_compose_seconds": 0.0,
        "encode_write_seconds": 0.0,
    }
    frame_index = 0
    mask_areas: list[float] = []
    hashes: list[str] = []
    previous_mask = None
    previous_area: float | None = None
    temporal_fallbacks = 0
    temporal_fallback_samples: list[str] = []
    started = time.perf_counter()
    try:
        while True:
            read_started = time.perf_counter()
            ok, frame = capture.read()
            timings["frame_read_seconds"] += elapsed(read_started)
            if not ok or frame is None:
                break

            mask_started = time.perf_counter()
            bg_mask = refined_background_mask(frame, bg_lab, threshold)
            fallback_reason = mask_needs_temporal_fallback(bg_mask, previous_area)
            if fallback_reason and previous_mask is not None:
                bg_mask = previous_mask
                temporal_fallbacks += 1
                if len(temporal_fallback_samples) < 8:
                    temporal_fallback_samples.append(f"{frame_index}:{fallback_reason}")
            else:
                previous_mask = bg_mask
                previous_area = float(bg_mask.mean())
            rgba = rgba_from_mask(frame, bg_mask)
            mask_area = float(bg_mask.mean())
            timings["mask_generation_seconds"] += elapsed(mask_started)
            timings["rgba_compose_seconds"] += 0.0
            mask_areas.append(mask_area)

            if frame_index in {0, max(0, input_frame_count // 2), max(0, input_frame_count - 1)}:
                hashes.append(hashlib.sha1(rgba.tobytes()).hexdigest()[:12])

            write_started = time.perf_counter()
            encoder.stdin.write(rgba.tobytes())
            timings["encode_write_seconds"] += elapsed(write_started)

            frame_index += 1
            if frame_index == 1 or frame_index % 30 == 0:
                log(f"透過処理中: {frame_index}フレーム")
    except BrokenPipeError as exc:
        raise AutoAlphaError("透過動画エンコードに失敗しました。") from exc
    finally:
        capture.release()
        encoder.stdin.close()

    encoder_error = encoder.stderr.read().decode("utf-8", errors="replace") if encoder.stderr else ""
    encoder_code = encoder.wait()
    timings["encode_total_seconds"] = elapsed(started) - timings["frame_read_seconds"] - timings["mask_generation_seconds"]
    if encoder_code != 0:
        raise AutoAlphaError("透過動画エンコードに失敗しました。\n" + encoder_error[-1200:])
    if frame_index <= 1:
        raise AutoAlphaError("出力フレーム数が1以下です。静止画化の疑いがあるため失敗扱いにします。")

    warnings = []
    if input_frame_count and frame_index != input_frame_count:
        warnings.append(f"入力フレーム数と出力フレーム数が異なります: {input_frame_count} -> {frame_index}")
    if len(set(hashes)) <= 1 and frame_index > 2:
        warnings.append("確認用フレームハッシュが同一です。静止画化の疑いがあります。")
    if temporal_fallbacks:
        warnings.append(f"暴走マスクを直前フレームへ置換しました: {temporal_fallbacks}フレーム")
    if mask_areas:
        log(f"マスク面積: 平均 {mean(mask_areas):.3f}, 最小 {min(mask_areas):.3f}, 最大 {max(mask_areas):.3f}")
    if temporal_fallback_samples:
        log("時間方向補正: " + " / ".join(temporal_fallback_samples))
    log(f"フレーム数: 入力 {input_frame_count}, 出力 {frame_index}")
    log(f"フレームハッシュ: {', '.join(hashes)}")
    return {
        "output_frame_count": frame_index,
        "warnings": warnings,
        "timings": timings,
    }
