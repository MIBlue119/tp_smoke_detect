"""Deterministic metadata-only message transport for the CPU reference path."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from ...contracts import CandidateEnvelope


@dataclass(frozen=True, slots=True)
class PublishedCandidate:
    """A delivery receipt.  The candidate remains the canonical payload."""

    message_id: UUID
    candidate: CandidateEnvelope


Subscriber = Callable[[CandidateEnvelope], None]


class InMemoryMessageBus:
    """An ordered, synchronous bus with an MQTT-like publish boundary.

    No raw image data is accepted.  This makes accidental media leakage in
    tests visible while keeping replay independent from an external broker.
    """

    topic = "track.candidate.v1"

    def __init__(self) -> None:
        self._messages: list[PublishedCandidate] = []
        self._subscribers: dict[str, list[Subscriber]] = defaultdict(list)

    @property
    def messages(self) -> tuple[PublishedCandidate, ...]:
        return tuple(self._messages)

    @property
    def candidates(self) -> tuple[CandidateEnvelope, ...]:
        return tuple(message.candidate for message in self._messages)

    def subscribe(self, callback: Subscriber, *, topic: str = topic) -> None:
        self._subscribers[topic].append(callback)

    def publish(self, candidate: CandidateEnvelope, *, topic: str = topic) -> PublishedCandidate:
        if not isinstance(candidate, CandidateEnvelope):
            raise TypeError("candidate topic accepts CandidateEnvelope only")
        receipt = PublishedCandidate(message_id=uuid4(), candidate=candidate)
        self._messages.append(receipt)
        for callback in self._subscribers[topic]:
            callback(candidate)
        return receipt

    def drain(self) -> tuple[PublishedCandidate, ...]:
        messages = tuple(self._messages)
        self._messages.clear()
        return messages


__all__ = ["InMemoryMessageBus", "PublishedCandidate", "Subscriber"]
