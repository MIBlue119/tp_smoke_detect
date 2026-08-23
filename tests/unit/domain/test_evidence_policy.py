from tp_smoke_detect.contracts import DecisionOutcome
from tp_smoke_detect.domain.models.observations import DomainObservation
from tp_smoke_detect.domain.policy.evidence import EvidencePolicy, EvidencePolicyConfig


def test_aliases_from_same_family_count_once() -> None:
    policy = EvidencePolicy(EvidencePolicyConfig(min_persistence_ms=0))
    decision = policy.evaluate(
        [
            DomainObservation(
                timestamp_ns=0,
                positive_channels=frozenset({"cigarette", "vape", "object"}),
            )
        ],
        cycle_count=1,
    )
    assert decision.outcome is DecisionOutcome.REJECTED
    assert decision.reason_codes[0].value == "independent_evidence_insufficient"


def test_two_independent_channels_verify() -> None:
    policy = EvidencePolicy(EvidencePolicyConfig(min_persistence_ms=2_000))
    decision = policy.evaluate(
        [
            DomainObservation(
                timestamp_ns=0,
                persistence_ms=2_000,
                positive_channels=frozenset({"object", "smoke"}),
            )
        ],
        cycle_count=1,
    )
    assert decision.outcome is DecisionOutcome.VERIFIED
    assert decision.audio_eligibility is True
    assert decision.reason_codes[-1].value == "verified"
