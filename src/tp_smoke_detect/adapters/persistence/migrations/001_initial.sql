-- PostgreSQL production schema for API-102.
-- SQLite's local schema mirrors these append-only facts for the CPU profile.
CREATE TABLE IF NOT EXISTS decisions (
    decision_id UUID PRIMARY KEY,
    camera_id VARCHAR(128) NOT NULL,
    track_id VARCHAR(128) NOT NULL,
    outcome VARCHAR(16) NOT NULL,
    reason_codes JSONB NOT NULL,
    evidence_channels JSONB NOT NULL,
    model_revisions JSONB NOT NULL,
    policy_revision VARCHAR(128) NOT NULL,
    latency_ms DOUBLE PRECISION NOT NULL CHECK (latency_ms >= 0),
    mode VARCHAR(32) NOT NULL,
    audio_eligibility BOOLEAN NOT NULL,
    audio_outcome VARCHAR(64),
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_camera_created ON decisions(camera_id, created_at DESC);
CREATE INDEX IF NOT EXISTS decisions_outcome_created ON decisions(outcome, created_at DESC);

CREATE TABLE IF NOT EXISTS reviews (
    review_id UUID PRIMARY KEY,
    decision_id UUID NOT NULL REFERENCES decisions(decision_id),
    label VARCHAR(32) NOT NULL,
    actor VARCHAR(128) NOT NULL,
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS reviews_decision_created ON reviews(decision_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mode_changes (
    change_id UUID PRIMARY KEY,
    mode VARCHAR(32) NOT NULL,
    reason TEXT NOT NULL,
    actor VARCHAR(128) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS cameras (
    camera_id VARCHAR(128) PRIMARY KEY,
    revision VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id VARCHAR(128) PRIMARY KEY,
    path TEXT NOT NULL,
    media_type VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS retention_audits (
    audit_id BIGSERIAL PRIMARY KEY,
    artifact_id VARCHAR(128) NOT NULL,
    media_class VARCHAR(32) NOT NULL,
    action VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artifact_id, action, status)
);
CREATE INDEX IF NOT EXISTS retention_audits_artifact_created
    ON retention_audits(artifact_id, created_at DESC);

CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id UUID PRIMARY KEY,
    status VARCHAR(16) NOT NULL,
    idempotency_key VARCHAR(255) UNIQUE,
    result JSONB,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mutes (
    mute_id UUID PRIMARY KEY,
    scope VARCHAR(16) NOT NULL,
    scope_id VARCHAR(128),
    expires_at TIMESTAMPTZ,
    actor VARCHAR(128) NOT NULL,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload JSONB NOT NULL
);
