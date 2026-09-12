-- Room for the start-to-end and extend modes.
--
-- The CHECK written inline in 001 is the one place that cannot import app/modes.py,
-- so it is widened here for every mode we intend to allow rather than once per
-- mode - a migration per phase would mean three deploys that each have to land
-- before the code that uses them. Nothing creates the new modes yet; they are
-- simply legal.
--
-- r2v stays: rows already carry it, and a constraint that rejects existing data
-- cannot be applied at all.
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_mode_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_mode_check
    CHECK (mode IN ('t2v', 'i2v', 'r2v', 'flf2v', 'extend'));
