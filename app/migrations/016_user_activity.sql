-- When each person was last served, when their current stretch of use began,
-- and when they last changed something.
--
-- A session here is a signed cookie, not a row (app/auth.py), so there is
-- nothing to count to answer "who is on the app right now" - and that is the
-- question an admin has to answer before deciding a running GPU is an accident
-- rather than someone's work. Three columns on the user answer it without a
-- session table and without a row per request.
--
-- active_since is not "signed in at": it restarts after a real gap, so "active
-- for 20 minutes" means twenty minutes of use. last_action_at is separate from
-- last_seen_at on purpose - the client polls every few seconds, so a page left
-- open in front of an empty chair keeps last_seen_at fresh forever and only
-- last_action_at can tell an admin that nobody has actually done anything.
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS active_since   TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_action_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users (last_seen_at DESC NULLS LAST);
