-- A voice or audio track the clip must follow, as an upload key. The guide node
-- anchors it at frame 0, so the model syncs mouth, timing and expression to it.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS audio_key TEXT;
