"""Deterministic, evidence-first rendering for the private baseline demo.

This module deliberately does not import a model runtime.  It consumes the
immutable JSON emitted by extraction/fusion and renders every overlay from
that record.  A failed or empty result is still rendered with its bounded
state; no visual evidence is invented here.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .acquisition import sha256_file
from .contracts import DEMO_SCHEMA_VERSION, validate_video_annotation

BANNER = "BASELINE DEMO - NOT PRODUCTION QUALIFIED"
MAX_OUTPUT_BYTES = 50_000_000
_STATE_COLORS = {
    "candidate": (35, 190, 70),
    "insufficient_evidence": (235, 190, 35),
    "unclear": (230, 120, 35),
    "baseline_miss": (225, 45, 55),
}


class AnnotationError(ValueError):
    """Raised when immutable evidence or media does not meet the demo contract."""


@dataclass(frozen=True, slots=True)
class MediaProbe:
    width: int
    height: int
    frame_rate: str
    duration_seconds: float
    frame_count: int | None
    codec: str
    pixel_format: str
    audio_streams: int


@dataclass(frozen=True, slots=True)
class MediaReceipt:
    output_artifact_id: str
    output_sha256: str
    output_bytes: int
    source_sha256: str
    annotation_sha256: str
    width: int
    height: int
    frame_rate: str
    duration_seconds: float
    frame_count: int | None
    video_codec: str
    pixel_format: str
    audio_streams: int
    max_bytes: int
    size_ok: bool
    no_audio_ok: bool
    duration_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_artifact_id": self.output_artifact_id,
            "output_sha256": self.output_sha256,
            "output_bytes": self.output_bytes,
            "source_sha256": self.source_sha256,
            "annotation_sha256": self.annotation_sha256,
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "video_codec": self.video_codec,
            "pixel_format": self.pixel_format,
            "audio_streams": self.audio_streams,
            "max_bytes": self.max_bytes,
            "size_ok": self.size_ok,
            "no_audio_ok": self.no_audio_ok,
            "duration_ok": self.duration_ok,
        }


def _run(command: list[str]) -> str:
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise AnnotationError(f"command failed: {' '.join(command[:2])}: {detail[-1000:]}") from exc
    return result.stdout


def _parse_rate(value: str) -> float:
    try:
        return float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        raise AnnotationError(f"invalid frame rate: {value}") from None


def probe_media(path: Path) -> MediaProbe:
    """Read media properties using ffprobe without decoding or mutating it."""

    if not path.is_file():
        raise AnnotationError(f"media is missing: {path}")
    raw = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnnotationError("ffprobe returned invalid JSON") from exc
    streams = payload.get("streams", [])
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1:
        raise AnnotationError("media must contain exactly one video stream")
    stream = videos[0]
    rate = str(stream.get("r_frame_rate") or stream.get("avg_frame_rate") or "0/1")
    duration = float(stream.get("duration") or payload.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise AnnotationError("media duration is missing or non-positive")
    width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
    if width <= 0 or height <= 0 or _parse_rate(rate) <= 0:
        raise AnnotationError("media dimensions or frame rate are invalid")
    count_value = stream.get("nb_frames")
    frame_count = None
    if count_value not in (None, "N/A"):
        try:
            frame_count = int(count_value)
        except (TypeError, ValueError):
            frame_count = None
    return MediaProbe(
        width=width,
        height=height,
        frame_rate=rate,
        duration_seconds=duration,
        frame_count=frame_count,
        codec=str(stream.get("codec_name", "")),
        pixel_format=str(stream.get("pix_fmt", "")),
        audio_streams=len(audios),
    )


def load_annotation(path: Path) -> dict[str, Any]:
    """Load and validate the JSON evidence before it can drive any drawing."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnnotationError(f"annotation JSON cannot be read: {path}") from exc
    if not isinstance(payload, dict):
        raise AnnotationError("annotation root must be an object")
    errors = validate_video_annotation(payload)
    if errors:
        raise AnnotationError("annotation contract failed: " + "; ".join(errors))
    _validate_annotation_semantics(payload)
    return payload


def _validate_annotation_semantics(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != DEMO_SCHEMA_VERSION:
        raise AnnotationError("unsupported annotation schema")
    source = payload.get("source")
    runtime = payload.get("runtime")
    models = payload.get("models")
    if (
        not isinstance(source, Mapping)
        or not isinstance(runtime, Mapping)
        or not isinstance(models, list)
    ):
        raise AnnotationError("annotation source, runtime, and models are required")
    if runtime.get("fake_provider") is not False or runtime.get("device_index", -1) < 0:
        raise AnnotationError("annotation runtime must identify a real CUDA device")
    if len(models) < 2:
        raise AnnotationError("annotation must contain pose and cigarette model receipts")
    frames = payload.get("frames", [])
    events = payload.get("events", [])
    if not isinstance(frames, list) or not isinstance(events, list):
        raise AnnotationError("frames and events must be arrays")
    for frame in frames:
        if not isinstance(frame, Mapping):
            raise AnnotationError("frame entries must be objects")
        for key in ("frame_index", "source_pts_ns", "state"):
            if key not in frame:
                raise AnnotationError(f"frame is missing {key}")
        if frame["state"] not in _STATE_COLORS:
            raise AnnotationError(f"unsupported frame state: {frame['state']}")
    source_duration_ns = float(source.get("duration_seconds", 0)) * 1_000_000_000
    for event in events:
        if not isinstance(event, Mapping):
            raise AnnotationError("event entries must be objects")
        if event.get("end_pts_ns", -1) < event.get("start_pts_ns", 0):
            raise AnnotationError("event interval is reversed")
        if event.get("end_pts_ns", 0) > source_duration_ns + 1_000_000_000:
            raise AnnotationError("event interval exceeds source duration")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ):
        if Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _box(
    draw: ImageDraw.ImageDraw,
    box: Any,
    width: int,
    height: int,
    color: tuple[int, int, int],
    label: str,
) -> None:
    if not isinstance(box, list | tuple) or len(box) != 4:
        return
    x1, y1, x2, y2 = (float(value) for value in box)
    coords = (round(x1 * width), round(y1 * height), round(x2 * width), round(y2 * height))
    draw.rectangle(coords, outline=color, width=max(2, round(min(width, height) / 240)))
    if label:
        draw.text((coords[0] + 3, max(0, coords[1] - 18)), label, fill=color, font=_font(14))


def _draw_frame(
    image: Image.Image,
    entries: list[Mapping[str, Any]],
    payload: Mapping[str, Any],
    frame_metadata: Mapping[str, Any] | None = None,
) -> None:
    draw = ImageDraw.Draw(image, "RGBA")
    width, height = image.size
    runtime = payload["runtime"]
    models = payload["models"]

    metadata = frame_metadata or (entries[0] if entries else {})
    frame_index = metadata.get("frame_index", "?")
    pts_ns = metadata.get("source_pts_ns")
    timestamp = (
        f"t={float(pts_ns) / 1_000_000_000:.3f}s"
        if isinstance(pts_ns, int | float)
        else "t=unknown"
    )
    run_id = str(payload.get("run_id", "unknown"))
    draw.rectangle((0, 0, width, 96), fill=(0, 0, 0, 215))
    draw.text((8, 5), BANNER, fill=(255, 235, 80), font=_font(18))
    draw.text((8, 27), f"run_id={run_id}", fill=(240, 240, 240), font=_font(11))
    draw.text(
        (8, 42),
        f"frame={frame_index} {timestamp} | GPU {runtime.get('device_name', 'unknown')}",
        fill=(240, 240, 240),
        font=_font(11),
    )
    for index, item in enumerate(models):
        draw.text(
            (8, 57 + index * 14),
            f"{item.get('role', 'unknown')} revision={item.get('model_revision', 'unknown')}",
            fill=(220, 220, 220),
            font=_font(10),
        )
    # Keep the overlay readable on crowded frames.  Candidate/unclear entries
    # win over low-value unmatched boxes; every omitted item remains in the
    # immutable JSON evidence.
    ranked = sorted(
        entries,
        key=lambda entry: (
            {"candidate": 0, "unclear": 1, "insufficient_evidence": 2, "baseline_miss": 3}.get(
                str(entry.get("state")), 4
            ),
            -float(entry.get("association_score") or 0),
        ),
    )[:4]
    summary_lines: list[str] = []
    for entry in ranked:
        state = str(entry.get("state", "insufficient_evidence"))
        color = _STATE_COLORS.get(state, (220, 220, 220))
        track = entry.get("track_id") or "untracked"
        person_conf = entry.get("person_confidence")
        cigarette_conf = entry.get("cigarette_confidence")
        score = entry.get("association_score")
        scores = " ".join(
            f"{name}={value:.2f}"
            for name, value in (
                ("p", person_conf),
                ("c", cigarette_conf),
                ("a", score),
            )
            if isinstance(value, float | int)
        )
        _box(draw, entry.get("person_box"), width, height, color, f"{track} {scores}")
        _box(draw, entry.get("cigarette_box"), width, height, (255, 90, 210), "cigarette")
        for point in entry.get("pose_keypoints", []):
            if isinstance(point, list | tuple) and len(point) >= 3 and float(point[2]) > 0:
                x, y = round(float(point[0]) * width), round(float(point[1]) * height)
                radius = max(2, round(min(width, height) / 180))
                draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        reason = ",".join(str(item) for item in entry.get("reason_codes", []))
        text = f"{track} {state}"
        if reason:
            text += f" reason={reason}"
        summary_lines.append(text)
    if not summary_lines:
        summary_lines.append("no person-cigarette association; evidence retained in JSON")
    panel_height = 24 + len(summary_lines) * 18
    draw.rectangle((8, height - panel_height - 8, width - 8, height - 8), fill=(0, 0, 0, 190))
    draw.text(
        (14, height - panel_height + 1),
        "SUMMARY (max 4 overlays)",
        fill=(255, 235, 80),
        font=_font(12),
    )
    for index, line in enumerate(summary_lines):
        draw.text(
            (14, height - panel_height + 20 + index * 18),
            line[:120],
            fill=(235, 235, 235),
            font=_font(11),
        )


def _encode_frames(
    source: Path, output: Path, payload: Mapping[str, Any], probe: MediaProbe, crf: int
) -> int:
    grouped: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    metadata_by_frame: dict[int, Mapping[str, Any]] = {}
    for entry in payload.get("frames", []):
        grouped[int(entry["frame_index"])].append(entry)
        metadata_by_frame.setdefault(int(entry["frame_index"]), entry)
    frame_bytes = probe.width * probe.height * 3
    decoder = subprocess.Popen(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-vsync",
            "0",
            "pipe:1",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{probe.width}x{probe.height}",
            "-r",
            probe.frame_rate,
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(crf),
            "-movflags",
            "+faststart",
            str(output),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    count = 0
    assert decoder.stdout is not None and encoder.stdin is not None
    try:
        while raw := decoder.stdout.read(frame_bytes):
            if len(raw) != frame_bytes:
                raise AnnotationError("source decoder returned a partial frame")
            image = Image.frombytes("RGB", (probe.width, probe.height), raw)
            _draw_frame(image, grouped.get(count, []), payload, metadata_by_frame.get(count))
            encoder.stdin.write(image.tobytes())
            count += 1
    finally:
        decoder.stdout.close()
        encoder.stdin.close()
    decoder_return = decoder.wait()
    encoder_return = encoder.wait()
    if decoder_return != 0 or encoder_return != 0:
        decoder_error = (decoder.stderr.read() if decoder.stderr else b"").decode(errors="replace")
        encoder_error = (encoder.stderr.read() if encoder.stderr else b"").decode(errors="replace")
        raise AnnotationError(
            f"video pipeline failed: {decoder_error[-500:]} {encoder_error[-500:]}"
        )
    if count == 0:
        raise AnnotationError("source contained no decodable frames")
    return count


def render_video(
    source: Path,
    annotation: Path,
    output: Path,
    *,
    max_bytes: int = MAX_OUTPUT_BYTES,
) -> MediaReceipt:
    """Render an evidence JSON into an atomic, silent H.264/yuv420p MP4."""

    if max_bytes <= 0:
        raise AnnotationError("max_bytes must be positive")
    payload = load_annotation(annotation)
    source_probe = probe_media(source)
    expected_source = payload["source"]
    if sha256_file(source) != expected_source["sha256"]:
        raise AnnotationError("source SHA-256 does not match annotation receipt")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths: list[Path] = []
    selected: Path | None = None
    try:
        for crf in (20, 24, 28, 32, 36, 40):
            fd, name = tempfile.mkstemp(prefix=f".{output.stem}.", suffix=".mp4", dir=output.parent)
            os.close(fd)
            candidate = Path(name)
            temporary_paths.append(candidate)
            _encode_frames(source, candidate, payload, source_probe, crf)
            if candidate.stat().st_size <= max_bytes:
                selected = candidate
                break
        if selected is None:
            raise AnnotationError(f"render exceeds configured size limit: {max_bytes} bytes")
        os.replace(selected, output)
        for path in temporary_paths:
            path.unlink(missing_ok=True)
    except Exception:
        for path in temporary_paths:
            path.unlink(missing_ok=True)
        raise
    return validate_media(source, annotation, output, max_bytes=max_bytes)


def validate_media(
    source: Path, annotation: Path, output: Path, *, max_bytes: int = MAX_OUTPUT_BYTES
) -> MediaReceipt:
    """Validate output stream, size, duration, dimensions, and hash linkage."""

    payload = load_annotation(annotation)
    source_probe = probe_media(source)
    expected_source = payload["source"]
    if sha256_file(source) != expected_source["sha256"]:
        raise AnnotationError("source SHA-256 does not match annotation receipt")
    if source.stat().st_size != expected_source["byte_size"]:
        raise AnnotationError("source byte size does not match annotation receipt")
    if (
        source_probe.width != expected_source["width"]
        or source_probe.height != expected_source["height"]
        or source_probe.frame_rate != expected_source["frame_rate"]
    ):
        raise AnnotationError("source media properties do not match annotation receipt")
    if source_probe.audio_streams != 0:
        raise AnnotationError("source must not contain an audio stream")
    result_probe = probe_media(output)
    output_size = output.stat().st_size
    frame_duration = 1 / _parse_rate(source_probe.frame_rate)
    duration_ok = (
        abs(result_probe.duration_seconds - source_probe.duration_seconds) <= frame_duration
    )
    no_audio_ok = result_probe.audio_streams == 0
    size_ok = output_size < max_bytes
    if result_probe.codec != "h264" or result_probe.pixel_format != "yuv420p":
        raise AnnotationError("output must be H.264 with yuv420p pixel format")
    if not size_ok:
        raise AnnotationError(f"output is not below the size limit: {output_size} >= {max_bytes}")
    if not no_audio_ok:
        raise AnnotationError("output must not contain an audio stream")
    if not duration_ok:
        raise AnnotationError("output duration differs from source by more than one frame")
    if result_probe.width != source_probe.width or result_probe.height != source_probe.height:
        raise AnnotationError("output dimensions differ from source")
    if result_probe.frame_rate != source_probe.frame_rate:
        raise AnnotationError("output frame rate differs from source")
    if (
        source_probe.frame_count is not None
        and result_probe.frame_count is not None
        and result_probe.frame_count != source_probe.frame_count
    ):
        raise AnnotationError("output frame count differs from source")
    return MediaReceipt(
        output_artifact_id=output.name,
        output_sha256=sha256_file(output),
        output_bytes=output_size,
        source_sha256=sha256_file(source),
        annotation_sha256=hashlib.sha256(annotation.read_bytes()).hexdigest(),
        width=result_probe.width,
        height=result_probe.height,
        frame_rate=result_probe.frame_rate,
        duration_seconds=result_probe.duration_seconds,
        frame_count=result_probe.frame_count,
        video_codec=result_probe.codec,
        pixel_format=result_probe.pixel_format,
        audio_streams=result_probe.audio_streams,
        max_bytes=max_bytes,
        size_ok=size_ok,
        no_audio_ok=no_audio_ok,
        duration_ok=duration_ok,
    )


def write_receipt(path: Path, receipt: MediaReceipt) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(receipt.to_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


__all__ = [
    "AnnotationError",
    "BANNER",
    "MAX_OUTPUT_BYTES",
    "MediaProbe",
    "MediaReceipt",
    "load_annotation",
    "probe_media",
    "render_video",
    "validate_media",
    "write_receipt",
]
