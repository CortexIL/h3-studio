-- Sound becomes a per-clip choice instead of a per-install one.
--
-- NULL means "whatever this install does", which is what every clip made before
-- the switch existed was rendered under - so old rows keep their meaning instead
-- of being retold as an explicit choice nobody made. A column defaulting to TRUE
-- would claim an install running KEEP_AUDIO=false had asked for sound.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS keep_audio BOOLEAN;
