-- A profile picture: the object key of a 256px WebP, or NULL for none.
ALTER TABLE users ADD COLUMN avatar_key TEXT;
