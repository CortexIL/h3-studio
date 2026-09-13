-- Sound direction: what the clip should sound like, kept apart from the shot.
--
-- H3 renders the soundtrack in the same pass as the picture and reads a
-- separate soundscape and music description far better than sound words buried
-- in the shot description. Stored as their own columns so "Use again" restores
-- them into their own fields instead of pasting them into the prompt box.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sound TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS music TEXT;
