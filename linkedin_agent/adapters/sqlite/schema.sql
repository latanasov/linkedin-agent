CREATE TABLE IF NOT EXISTS leads (
  id TEXT PRIMARY KEY,
  campaign TEXT NOT NULL,
  linkedin_url TEXT NOT NULL UNIQUE,
  first_name TEXT, last_name TEXT, company TEXT, title TEXT, email TEXT,
  location TEXT, timezone TEXT,
  custom_fields TEXT NOT NULL DEFAULT '{}',
  profile TEXT NOT NULL DEFAULT '{}',
  posts TEXT NOT NULL DEFAULT '[]',
  stage TEXT NOT NULL DEFAULT 'new',
  invited_at TEXT, connected_at TEXT, last_touch_at TEXT,
  last_message_at TEXT, last_message_text TEXT, prior_reply_text TEXT, replied_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS leads_stage ON leads(campaign, stage);

CREATE TABLE IF NOT EXISTS lead_sequences (
  lead_id TEXT PRIMARY KEY REFERENCES leads(id) ON DELETE CASCADE,
  campaign TEXT NOT NULL,
  step_id TEXT,
  branch TEXT,
  next_due_at TEXT,
  step_entered_at TEXT,
  history TEXT NOT NULL DEFAULT '[]',
  paused INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS lead_sequences_due ON lead_sequences(next_due_at);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  lead_id TEXT REFERENCES leads(id) ON DELETE CASCADE,
  step_id TEXT,
  action TEXT NOT NULL,
  profile_url TEXT NOT NULL,
  account TEXT NOT NULL,
  params TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued',
  attempts INTEGER NOT NULL DEFAULT 0,
  not_before TEXT, not_after TEXT,
  body_hash TEXT,
  result TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT, finished_at TEXT,
  claimed_by INTEGER
);
CREATE INDEX IF NOT EXISTS tasks_pick ON tasks(account, status, not_before, created_at);
CREATE INDEX IF NOT EXISTS tasks_lead_step ON tasks(lead_id, step_id, status);
CREATE INDEX IF NOT EXISTS tasks_body ON tasks(account, body_hash, finished_at);

CREATE TABLE IF NOT EXISTS action_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  action TEXT NOT NULL,
  lead_id TEXT,
  at TEXT NOT NULL,
  ok INTEGER NOT NULL,
  result_status TEXT
);
CREATE INDEX IF NOT EXISTS action_log_window ON action_log(account, action, at);
CREATE INDEX IF NOT EXISTS action_log_lead ON action_log(lead_id, at);

CREATE TABLE IF NOT EXISTS review_queue (
  task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  context TEXT NOT NULL,
  draft TEXT NOT NULL,
  approved_text TEXT,
  decided_at TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
  name TEXT PRIMARY KEY,
  first_action_at TEXT,
  logged_in_at TEXT,
  user_agent TEXT,
  tripped_until TEXT,
  trip_reason TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  session_expired_at TEXT,
  governor_state TEXT NOT NULL DEFAULT 'normal',
  governor_checked_at TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
