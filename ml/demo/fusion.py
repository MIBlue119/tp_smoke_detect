"""Deterministic spatial and temporal fusion for the private demo.

This module deliberately sits below the production decision policy.  It turns
typed pose and cigarette detections into auditable evidence and *candidate*
intervals; it never emits a production ``verified`` decision or requests
audio.  All state is driven by source PTS, so replaying the same frames gives
the same IDs, ordering, and state transitions.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable
from dataclasses import dataclass

from .contracts import EventInterval, EventState, FrameEvidence, canonical_json

Box = tuple[float, float, float, float]
Keypoint = tuple[float, float, float]

NOSE = 0
LEFT_WRIST = 9
RIGHT_WRIST = 10


def _bounded(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a finite normalized value")


def _box(value: Box, name: str) -> None:
    if len(value) != 4:
        raise ValueError(f"{name} must contain x1, y1, x2, y2")
    x1, y1, x2, y2 = value
    for item in value:
        _bounded(item, name)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"{name} must have positive area")


@dataclass(frozen=True, slots=True)
class PersonDetection:
    """One pose detector output in normalized image coordinates.

    ``track_id`` is optional because the pose adapter may not have a tracker.
    :class:`DeterministicPersonTracker` fills it using IoU and a stable
    ``track-000001`` sequence before fusion.
    """

    box: Box
    keypoints: tuple[Keypoint, ...]
    confidence: float
    track_id: str | None = None

    def __post_init__(self) -> None:
        _box(self.box, "person box")
        _bounded(self.confidence, "person confidence")
        if self.track_id is not None and not self.track_id.strip():
            raise ValueError("track_id cannot be blank")
        for point in self.keypoints:
            if len(point) != 3:
                raise ValueError("pose keypoints must contain x, y, confidence")
            _bounded(point[0], "keypoint x")
            _bounded(point[1], "keypoint y")
            _bounded(point[2], "keypoint confidence")


@dataclass(frozen=True, slots=True)
class CigaretteDetection:
    box: Box
    confidence: float
    detection_index: int = 0

    def __post_init__(self) -> None:
        _box(self.box, "cigarette box")
        _bounded(self.confidence, "cigarette confidence")
        if self.detection_index < 0:
            raise ValueError("detection_index must be non-negative")


@dataclass(frozen=True, slots=True)
class FrameDetections:
    frame_index: int
    source_pts_ns: int
    persons: tuple[PersonDetection, ...] = ()
    cigarettes: tuple[CigaretteDetection, ...] = ()

    def __post_init__(self) -> None:
        if self.frame_index < 0 or self.source_pts_ns < 0:
            raise ValueError("frame index and source PTS must be non-negative")


@dataclass(frozen=True, slots=True)
class FusionConfig:
    """Frozen, hashable thresholds used by the baseline demo."""

    config_revision: str = "shibuya-baseline-r1"
    entry_confidence: float = 0.55
    exit_confidence: float = 0.35
    persistence_frames: int = 4
    exit_gap_frames: int = 6
    association_threshold: float = 0.35
    max_track_gap_frames: int = 6
    iou_match_threshold: float = 0.20
    pose_confidence: float = 0.20

    def __post_init__(self) -> None:
        if not self.config_revision.strip():
            raise ValueError("config_revision is required")
        for name in (
            "entry_confidence",
            "exit_confidence",
            "association_threshold",
            "iou_match_threshold",
            "pose_confidence",
        ):
            _bounded(getattr(self, name), name)
        if self.exit_confidence > self.entry_confidence:
            raise ValueError("exit_confidence must not exceed entry_confidence")
        for name in ("persistence_frames", "exit_gap_frames", "max_track_gap_frames"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class FusionResult:
    frames: tuple[FrameEvidence, ...]
    events: tuple[EventInterval, ...]

    def for_rendering(self) -> tuple[FrameEvidence, ...]:
        """Return stable evidence ordering for the annotation renderer."""

        return tuple(sorted(self.frames, key=lambda item: (item.frame_index, item.track_id or "")))


@dataclass(slots=True)
class _ActiveTrack:
    track_id: str
    box: Box
    last_frame_index: int
    missed_frames: int = 0


class DeterministicPersonTracker:
    """Small source-order IoU tracker with deterministic tie-breaking.

    Detector-supplied IDs are trusted only when non-empty.  Otherwise matches
    are one-to-one, highest IoU first, then lexical track ID and input index.
    This is intentionally a demo tracker, not a site-quality identity model.
    """

    def __init__(self, iou_match_threshold: float = 0.20, max_gap_frames: int = 6) -> None:
        _bounded(iou_match_threshold, "iou_match_threshold")
        if max_gap_frames < 1:
            raise ValueError("max_gap_frames must be positive")
        self.iou_match_threshold = iou_match_threshold
        self.max_gap_frames = max_gap_frames
        self._tracks: dict[str, _ActiveTrack] = {}
        self._next_id = 1

    def _new_id(self) -> str:
        track_id = f"track-{self._next_id:06d}"
        self._next_id += 1
        return track_id

    def update(self, frame: FrameDetections) -> tuple[PersonDetection, ...]:
        """Attach stable IDs to persons in this source frame."""

        for track in self._tracks.values():
            track.missed_frames += 1
        supplied: list[PersonDetection] = []
        anonymous: list[tuple[int, PersonDetection]] = []
        for index, person in enumerate(frame.persons):
            if person.track_id is not None:
                supplied.append(person)
            else:
                anonymous.append((index, person))

        result = list(supplied)
        # A supplied ID is authoritative for this frame, but stale tracks are
        # removed below so an ID switch cannot silently join old evidence.
        for person in supplied:
            self._tracks[person.track_id or self._new_id()] = _ActiveTrack(
                person.track_id or self._new_id(), person.box, frame.frame_index, 0
            )

        pairs: list[tuple[float, str, int, int]] = []
        for input_index, person in anonymous:
            for track_id, track in self._tracks.items():
                if track.missed_frames > self.max_gap_frames:
                    continue
                overlap = _iou(person.box, track.box)
                if overlap >= self.iou_match_threshold:
                    pairs.append((-overlap, track_id, input_index, input_index))
        used_tracks: set[str] = set()
        used_inputs: set[int] = set()
        for _, track_id, input_index, _ in sorted(pairs):
            if track_id in used_tracks or input_index in used_inputs:
                continue
            person = next(item for idx, item in anonymous if idx == input_index)
            tracked = PersonDetection(person.box, person.keypoints, person.confidence, track_id)
            result.append(tracked)
            self._tracks[track_id].box = person.box
            self._tracks[track_id].last_frame_index = frame.frame_index
            self._tracks[track_id].missed_frames = 0
            used_tracks.add(track_id)
            used_inputs.add(input_index)
        for input_index, person in anonymous:
            if input_index in used_inputs:
                continue
            track_id = self._new_id()
            result.append(
                PersonDetection(person.box, person.keypoints, person.confidence, track_id)
            )
            self._tracks[track_id] = _ActiveTrack(track_id, person.box, frame.frame_index, 0)
        self._tracks = {
            track_id: track
            for track_id, track in self._tracks.items()
            if track.missed_frames <= self.max_gap_frames
        }
        return tuple(sorted(result, key=lambda item: item.track_id or ""))


def _iou(left: Box, right: Box) -> float:
    x1 = max(left[0], right[0])
    y1 = max(left[1], right[1])
    x2 = min(left[2], right[2])
    y2 = min(left[3], right[3])
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    left_area = (left[2] - left[0]) * (left[3] - left[1])
    right_area = (right[2] - right[0]) * (right[3] - right[1])
    return intersection / (left_area + right_area - intersection)


def _center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _distance_to_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> float:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_squared = dx * dx + dy * dy
    if length_squared == 0:
        return math.dist(point, start)
    ratio = max(
        0.0, min(1.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared)
    )
    return math.dist(point, (start[0] + ratio * dx, start[1] + ratio * dy))


def _point(person: PersonDetection, index: int, minimum: float) -> tuple[float, float] | None:
    if index >= len(person.keypoints):
        return None
    x, y, confidence = person.keypoints[index]
    return (x, y) if confidence >= minimum else None


def _association_score(
    person: PersonDetection, cigarette: CigaretteDetection, config: FusionConfig
) -> float | None:
    """Score head or wrist-to-mouth spatial association in [0, 1]."""

    nose = _point(person, NOSE, config.pose_confidence)
    if nose is None:
        return None
    cx, cy = _center(cigarette.box)
    x1, y1, x2, y2 = person.box
    width, height = x2 - x1, y2 - y1
    # Head zone is deliberately broad enough for a small cigarette box while
    # remaining inside the person's upper body box.
    head = (x1 - width * 0.12, y1 - height * 0.12, x2 + width * 0.12, y1 + height * 0.50)
    in_head = head[0] <= cx <= head[2] and head[1] <= cy <= head[3]
    head_score = max(0.0, 1.0 - math.dist((cx, cy), nose) / max(width, height)) if in_head else 0.0
    wrist_scores: list[float] = []
    for wrist_index in (LEFT_WRIST, RIGHT_WRIST):
        wrist = _point(person, wrist_index, config.pose_confidence)
        if wrist is None:
            continue
        distance = _distance_to_segment((cx, cy), nose, wrist)
        corridor = max(width, height) * 0.18
        if distance <= corridor:
            wrist_scores.append(max(0.0, 1.0 - distance / corridor))
    geometry = max([head_score, *wrist_scores], default=0.0)
    if geometry <= 0:
        return None
    return min(1.0, geometry * cigarette.confidence * person.confidence)


@dataclass(slots=True)
class _TemporalState:
    positive_frames: int = 0
    missing_frames: int = 0
    active: bool = False
    start_pts_ns: int | None = None
    last_positive_pts_ns: int | None = None


class DeterministicFusion:
    """Fuse ordered frame detections into evidence and candidate intervals."""

    def __init__(self, config: FusionConfig | None = None) -> None:
        self.config = config or FusionConfig()
        self.tracker = DeterministicPersonTracker(
            self.config.iou_match_threshold, self.config.max_track_gap_frames
        )
        self._states: dict[str, _TemporalState] = {}
        self._events: list[EventInterval] = []
        self._last_pts_ns: int | None = None

    def _event_id(self, track_id: str, start_pts_ns: int) -> str:
        payload = canonical_json(
            {
                "config_revision": self.config.config_revision,
                "track_id": track_id,
                "start_pts_ns": start_pts_ns,
            }
        )
        return "demo-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]

    def _close(
        self, track_id: str, reason_codes: tuple[str, ...] = ("associated_cigarette",)
    ) -> None:
        state = self._states[track_id]
        if not state.active or state.start_pts_ns is None or state.last_positive_pts_ns is None:
            return
        self._events.append(
            EventInterval(
                event_id=self._event_id(track_id, state.start_pts_ns),
                track_id=track_id,
                start_pts_ns=state.start_pts_ns,
                end_pts_ns=state.last_positive_pts_ns,
                state=EventState.CANDIDATE,
                reason_codes=reason_codes,
            )
        )
        state.active = False
        state.start_pts_ns = None
        state.last_positive_pts_ns = None
        state.positive_frames = 0
        state.missing_frames = 0

    def process(self, frames: Iterable[FrameDetections]) -> FusionResult:
        evidence: list[FrameEvidence] = []
        for frame in frames:
            evidence.extend(self.process_frame(frame))
        for track_id in sorted(self._states):
            self._close(track_id)
        return FusionResult(
            tuple(evidence), tuple(sorted(self._events, key=lambda item: item.event_id))
        )

    def process_frame(self, frame: FrameDetections) -> tuple[FrameEvidence, ...]:
        if self._last_pts_ns is not None and frame.source_pts_ns < self._last_pts_ns:
            self._last_pts_ns = max(self._last_pts_ns, frame.source_pts_ns)
            return tuple(
                FrameEvidence(
                    frame.frame_index,
                    frame.source_pts_ns,
                    None,
                    None,
                    (),
                    None,
                    None,
                    None,
                    None,
                    EventState.UNCLEAR,
                    ("out_of_order_pts",),
                )
                for _ in [0]
            )
        self._last_pts_ns = frame.source_pts_ns
        persons = self.tracker.update(frame)
        candidates: list[tuple[float, str, int, PersonDetection, CigaretteDetection]] = []
        for person in persons:
            for cig_index, cigarette in enumerate(frame.cigarettes):
                score = _association_score(person, cigarette, self.config)
                if score is not None and score >= self.config.association_threshold:
                    candidates.append((-score, person.track_id or "", cig_index, person, cigarette))
        selected: dict[str, tuple[float, CigaretteDetection]] = {}
        used_cigarettes: set[int] = set()
        for negative_score, track_id, cig_index, _person, cigarette in sorted(candidates):
            if track_id in selected or cig_index in used_cigarettes:
                continue
            selected[track_id] = (-negative_score, cigarette)
            used_cigarettes.add(cig_index)

        output: list[FrameEvidence] = []
        for person in persons:
            person_track_id = person.track_id
            if person_track_id is None:
                continue
            state = self._states.setdefault(person_track_id, _TemporalState())
            selected_item = selected.get(person_track_id)
            if selected_item is None:
                state.missing_frames += 1
                state.positive_frames = 0 if not state.active else state.positive_frames
                if state.active and state.missing_frames > self.config.exit_gap_frames:
                    self._close(person_track_id, ("associated_cigarette", "track_gap"))
                frame_state = (
                    EventState.UNCLEAR if state.active else EventState.INSUFFICIENT_EVIDENCE
                )
                reasons: tuple[str, ...] = (
                    ("missing_pose",) if len(person.keypoints) == 0 else ("unmatched_cigarette",)
                )
                if state.active:
                    reasons = ("occluded", "track_gap")
                output.append(
                    FrameEvidence(
                        frame.frame_index,
                        frame.source_pts_ns,
                        person_track_id,
                        person.box,
                        person.keypoints,
                        None,
                        person.confidence,
                        None,
                        None,
                        frame_state,
                        reasons,
                    )
                )
                continue
            score, cigarette = selected_item
            # Hysteresis is deliberate: an inactive track enters only at the
            # higher entry threshold; an active track remains positive only
            # while it meets the lower exit threshold.  A low score is
            # evidence, but never a candidate, and is allowed to close after
            # the same bounded gap as an occlusion.
            if state.active and score < self.config.exit_confidence:
                state.missing_frames += 1
                if state.missing_frames > self.config.exit_gap_frames:
                    self._close(person_track_id, ("associated_cigarette", "below_threshold"))
                output.append(
                    FrameEvidence(
                        frame.frame_index,
                        frame.source_pts_ns,
                        person_track_id,
                        person.box,
                        person.keypoints,
                        cigarette.box,
                        person.confidence,
                        cigarette.confidence,
                        score,
                        EventState.UNCLEAR,
                        ("below_threshold",),
                    )
                )
                continue
            state.missing_frames = 0
            # Persistence is an entry gate: weak evidence must not accumulate
            # toward activation. Once active, the lower exit threshold keeps
            # the event alive without inflating the next entry streak.
            if not state.active:
                if score >= self.config.entry_confidence:
                    state.positive_frames += 1
                else:
                    state.positive_frames = 0
            if (
                not state.active
                and state.positive_frames >= self.config.persistence_frames
                and score >= self.config.entry_confidence
            ):
                state.active = True
                state.start_pts_ns = frame.source_pts_ns
            if state.active:
                state.last_positive_pts_ns = frame.source_pts_ns
                frame_state = EventState.CANDIDATE
            else:
                frame_state = EventState.INSUFFICIENT_EVIDENCE
            output.append(
                FrameEvidence(
                    frame.frame_index,
                    frame.source_pts_ns,
                    person_track_id,
                    person.box,
                    person.keypoints,
                    cigarette.box,
                    person.confidence,
                    cigarette.confidence,
                    score,
                    frame_state,
                    ("associated_cigarette",) if state.active else ("below_threshold",),
                )
            )
        seen_tracks = {person.track_id for person in persons if person.track_id is not None}
        for track_id, state in self._states.items():
            if track_id in seen_tracks or not state.active:
                continue
            state.missing_frames += 1
            if state.missing_frames > self.config.exit_gap_frames:
                self._close(track_id, ("associated_cigarette", "track_gap"))
        if not persons and frame.cigarettes:
            output.append(
                FrameEvidence(
                    frame.frame_index,
                    frame.source_pts_ns,
                    None,
                    None,
                    (),
                    frame.cigarettes[0].box,
                    None,
                    frame.cigarettes[0].confidence,
                    None,
                    EventState.INSUFFICIENT_EVIDENCE,
                    ("unmatched_cigarette",),
                )
            )
        return tuple(output)


def baseline_miss_event(
    track_id: str, start_pts_ns: int, end_pts_ns: int, reason: str = "manual_review"
) -> EventInterval:
    """Create a manual-review baseline miss without changing raw evidence."""

    return EventInterval(
        event_id="manual-miss-"
        + hashlib.sha256(f"{track_id}:{start_pts_ns}:{end_pts_ns}".encode()).hexdigest()[:20],
        track_id=track_id,
        start_pts_ns=start_pts_ns,
        end_pts_ns=end_pts_ns,
        state=EventState.BASELINE_MISS,
        reason_codes=("baseline_miss", "manual_review")
        if reason == "manual_review"
        else ("baseline_miss",),
    )


__all__ = [
    "Box",
    "CigaretteDetection",
    "DeterministicFusion",
    "DeterministicPersonTracker",
    "FrameDetections",
    "FusionConfig",
    "FusionResult",
    "Keypoint",
    "PersonDetection",
    "baseline_miss_event",
]
