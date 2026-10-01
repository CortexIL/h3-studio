-- What people call a clip: "PERF-025-U01-B", "scene-25-B".
--
-- Kept beside the prompt, never in it - the prompt is the only text the model
-- sees, and a shot id written into it is an instruction the model reads. A
-- batch names it (`name`, or else the picture's filename); it is the clip's
-- download filename, its card's heading, and something the archive can search.
-- NULL for every clip queued before it existed, which keep their prompt names.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS label TEXT;
