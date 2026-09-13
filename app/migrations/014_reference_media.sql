-- References mode: reference videos and standalone audio clips, as lists of
-- upload keys next to the images in ref_images. Up to 3 of each.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ref_videos JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS ref_audios JSONB NOT NULL DEFAULT '[]'::jsonb;
