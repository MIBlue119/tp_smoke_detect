from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tp_smoke_detect.adapters.audio.fake import FakeAudioController
from tp_smoke_detect.contracts import AudioCommand
from tp_smoke_detect.ports.audio import PlaybackStatus


def _command(*, message_id: str = "smoke-reminder-neutral-01", expires: int = 30) -> AudioCommand:
    return AudioCommand(
        command_id=uuid4(),
        decision_id=uuid4(),
        zone_id="zone-a",
        message_id=message_id,
        volume_profile="default",
        expires_at=datetime.now(UTC) + timedelta(seconds=expires),
        policy_revision="p1",
    )


def test_fake_adapter_is_idempotent_and_catalogue_only() -> None:
    adapter = FakeAudioController()
    command = _command()
    first = adapter.send(command)
    duplicate = adapter.send(command)
    assert first.status is PlaybackStatus.ACCEPTED
    assert duplicate.status is PlaybackStatus.DUPLICATE
    assert len(adapter.commands) == 1

    unknown = adapter.send(_command(message_id="operator-text"))
    assert unknown.status is PlaybackStatus.REJECTED


def test_fake_adapter_rejects_expired_command() -> None:
    adapter = FakeAudioController(now=datetime.now(UTC))
    expired = _command(expires=-1)
    assert adapter.send(expired).status is PlaybackStatus.EXPIRED
