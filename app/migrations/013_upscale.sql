-- Upscale: a job made from a finished clip. The mode joins the CHECK; the
-- factor, the source's frame count (the graph is cut into chunks by it) and
-- the source clip's id ride on the row.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_mode_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_mode_check
    CHECK (mode IN ('t2v', 'i2v', 'r2v', 'flf2v', 'extend', 'upscale'));
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS upscale_factor INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_frames INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS source_job_id TEXT;
