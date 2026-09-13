-- Per-clip render controls on top of the preset. NULL = whatever the preset says,
-- which is exactly what every clip made before these existed got.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS steps INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS shift_video REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS shift_audio REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS width INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS height INTEGER;
