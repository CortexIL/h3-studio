-- Hiding a finished job from the studio feed must not remove it from the
-- archive: the archive is these same rows. So "remove from list" and "clear
-- finished" set this instead of deleting.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_jobs_feed ON jobs (user_id, created_at DESC)
    WHERE dismissed_at IS NULL;
