-- v2 schema migration: split analysis_status into orthogonal state tables.
-- Idempotent: safe to run multiple times (CREATE TABLE IF NOT EXISTS).
-- Legacy analysis_status table is PRESERVED (read-only projection during cutover).

-- =============================================================================
-- video_catalog: read-only metadata projection keyed by video_id
-- =============================================================================
CREATE TABLE IF NOT EXISTS video_catalog (
    video_id TEXT PRIMARY KEY,
    channel_id TEXT,
    title TEXT,
    description TEXT,
    thumbnail TEXT,
    duration INTEGER DEFAULT 0,
    privacy_status TEXT DEFAULT 'public',
    upload_status TEXT,
    is_live_content INTEGER DEFAULT 0,
    unavailable_reason TEXT,
    has_captions INTEGER,
    last_checked_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_video_catalog_channel_id
    ON video_catalog(channel_id);

-- =============================================================================
-- transcript_status: lifecycle of transcript acquisition
-- =============================================================================
CREATE TABLE IF NOT EXISTS transcript_status (
    video_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'absent',
    updated_at TEXT NOT NULL,
    source TEXT,
    last_stage TEXT,
    failure_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcript_status_status
    ON transcript_status(status);

-- =============================================================================
-- transcript_jobs: work queue for transcript pool
-- =============================================================================
CREATE TABLE IF NOT EXISTS transcript_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    profile TEXT DEFAULT 'standard',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    UNIQUE(video_id)
);

CREATE INDEX IF NOT EXISTS idx_transcript_jobs_created
    ON transcript_jobs(created_at);

-- =============================================================================
-- transcript_artifacts: output of transcript pool
-- =============================================================================
CREATE TABLE IF NOT EXISTS transcript_artifacts (
    video_id TEXT PRIMARY KEY,
    lang TEXT,
    source TEXT,
    content_hash TEXT,
    transcript_path TEXT,
    captured_at TEXT,
    stage_version INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_transcript_artifacts_hash
    ON transcript_artifacts(content_hash);

-- =============================================================================
-- transcript_attempts: append-only attempt log
-- =============================================================================
CREATE TABLE IF NOT EXISTS transcript_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    provider TEXT,
    outcome TEXT,
    latency_ms REAL,
    error_class TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_transcript_attempts_video
    ON transcript_attempts(video_id);

-- =============================================================================
-- visual_status: lifecycle of visual enrichment
-- =============================================================================
CREATE TABLE IF NOT EXISTS visual_status (
    video_id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'queued',
    updated_at TEXT NOT NULL,
    profile TEXT DEFAULT 'standard',
    last_stage TEXT,
    failure_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_visual_status_status
    ON visual_status(status);

-- =============================================================================
-- visual_jobs: work queue for visual pool
-- =============================================================================
CREATE TABLE IF NOT EXISTS visual_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    profile TEXT DEFAULT 'standard',
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    attempt_count INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_visual_jobs_status_created
    ON visual_jobs(completed_at, created_at);

-- =============================================================================
-- visual_attempts: append-only attempt log
-- =============================================================================
CREATE TABLE IF NOT EXISTS visual_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    profile TEXT,
    provider TEXT,
    outcome TEXT,
    latency_ms REAL,
    error_class TEXT,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_visual_attempts_video
    ON visual_attempts(video_id);

-- =============================================================================
-- visual_artifacts: output of visual pool (versioned)
-- Composite key: (video_id, version)
-- =============================================================================
CREATE TABLE IF NOT EXISTS visual_artifacts (
    video_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT,
    frames_dir TEXT,
    ocr_text TEXT,
    visual_tags TEXT,
    perceptual_hash_list TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (video_id, version)
);

CREATE INDEX IF NOT EXISTS idx_visual_artifacts_video_version
    ON visual_artifacts(video_id, version);

-- =============================================================================
-- analysis_jobs: work queue for assembly step
-- =============================================================================
CREATE TABLE IF NOT EXISTS analysis_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    profile TEXT DEFAULT 'standard',
    created_at TEXT NOT NULL,
    completed_at TEXT,
    assembled_version INTEGER
);

CREATE INDEX IF NOT EXISTS idx_analysis_jobs_created
    ON analysis_jobs(created_at);

-- =============================================================================
-- analysis_artifacts: assembled transcript+visual output (versioned)
-- Composite key: (video_id, version)
-- =============================================================================
CREATE TABLE IF NOT EXISTS analysis_artifacts (
    video_id TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT,
    body_path TEXT,
    sources_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (video_id, version)
);

-- =============================================================================
-- ingestion_receipts: idempotency log for downstream publish
-- =============================================================================
CREATE TABLE IF NOT EXISTS ingestion_receipts (
    video_id TEXT NOT NULL,
    downstream TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    published_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (video_id, downstream, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_receipts_video_downstream
    ON ingestion_receipts(video_id, downstream);

-- =============================================================================
-- migration_audit: single-row table recording migration health
-- =============================================================================
CREATE TABLE IF NOT EXISTS migration_audit (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    timestamp TEXT NOT NULL,
    transcript_status_count INTEGER,
    transcript_cache_count INTEGER,
    transcript_artifacts_count INTEGER,
    delta INTEGER,
    pass INTEGER
);

-- Schema version marker
INSERT OR REPLACE INTO migration_audit (id, timestamp, pass)
VALUES (1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 0);
