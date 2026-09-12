-- A queued job's place in its own owner's backlog, as a sortable number.
--
-- Seeded from created_at so a queue nobody has dragged drains in the order it
-- was asked for. Fair-share order (005) is unchanged: this only replaces
-- created_at as the key *inside* one owner's backlog, so reordering your own
-- clips cannot change how the pod is shared between people.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS queue_pos DOUBLE PRECISION;

UPDATE jobs SET queue_pos = EXTRACT(EPOCH FROM created_at) WHERE queue_pos IS NULL;

-- The sort the claim and the position queries now use. idx_jobs_queue_fair is
-- keyed on created_at and can no longer serve either.
CREATE INDEX IF NOT EXISTS idx_jobs_queue_pos
    ON jobs (user_id, queue_pos, id) WHERE status IN ('queued', 'running');
