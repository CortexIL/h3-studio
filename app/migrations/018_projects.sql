-- Projects: clips belong to a shared project instead of to one person alone.
--
-- A project has members with a role (owner, editor, viewer). Every clip that
-- already exists moves into a "My clips" project owned by whoever made it, so
-- nobody loses anything and nobody sees anyone else's clips by accident. People
-- with no clips start with no project; `projects.personal_for` makes one on
-- demand.
CREATE TABLE IF NOT EXISTS projects (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    personal   BOOLEAN NOT NULL DEFAULT FALSE
);
-- At most one "My clips" per person: where a clip sent without a project goes.
CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_one_personal ON projects (created_by) WHERE personal;
CREATE TABLE IF NOT EXISTS project_members (
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('owner','editor','viewer')),
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members (user_id);
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS project_id UUID REFERENCES projects(id) ON DELETE CASCADE;
-- One "My clips" per person who has clips; everyone else starts with none.
INSERT INTO projects (name, created_by, personal)
    SELECT 'My clips', u.user_id, TRUE FROM (SELECT DISTINCT user_id FROM jobs) u;
INSERT INTO project_members (project_id, user_id, role)
    SELECT id, created_by, 'owner' FROM projects;
UPDATE jobs SET project_id = p.id FROM projects p WHERE p.created_by = jobs.user_id;
ALTER TABLE jobs ALTER COLUMN project_id SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_project_feed ON jobs (project_id, created_at DESC)
    WHERE dismissed_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_project_archive ON jobs (project_id, finished_at DESC)
    WHERE status = 'done';
