-- One still frame per finished clip, so archive and feed cards show a picture
-- instead of a black box until someone presses play. Clips from before this
-- have none; the UI falls back to a placeholder.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS poster_key TEXT;
