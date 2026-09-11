-- Fair-share queue order asks, for each queued job, how many of the same owner's
-- pending jobs arrived before it. That is a per-owner scan of the pending rows,
-- and idx_jobs_queue is keyed on created_at alone over queued rows only, so it
-- can serve neither the partition nor the running half of the window.
CREATE INDEX IF NOT EXISTS idx_jobs_queue_fair
    ON jobs (user_id, created_at, id) WHERE status IN ('queued', 'running');
