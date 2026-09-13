-- Effect presets: the prompt embeddings a clip picked, by name. Up to 3.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS effects JSONB NOT NULL DEFAULT '[]'::jsonb;
