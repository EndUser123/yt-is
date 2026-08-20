-- =============================================================================
-- v3_visual_queue: deduplicate visual_jobs and enforce one job per video.
--
-- The v2 legacy coverage re-enqueue used INSERT OR IGNORE, but visual_jobs had
-- no UNIQUE constraint on video_id, so re-running the migration silently
-- duplicated queue rows. This migration removes duplicates (keeping the
-- lowest job_id per video) and adds a unique index so enqueue becomes
-- idempotent. Safe to run repeatedly.
-- =============================================================================

DELETE FROM visual_jobs
 WHERE job_id NOT IN (
     SELECT MIN(job_id) FROM visual_jobs GROUP BY video_id
 );

CREATE UNIQUE INDEX IF NOT EXISTS uq_visual_jobs_video
    ON visual_jobs(video_id);
