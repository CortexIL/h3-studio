-- Keyframes anywhere: images pinned at a moment inside the clip, as
-- [{"key": "<upload key>", "at": <seconds>}]. The first and last frames have
-- their own inputs on the model node; these go through the guide node, which
-- anchors a frame at any index.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS keyframes JSONB NOT NULL DEFAULT '[]'::jsonb;
