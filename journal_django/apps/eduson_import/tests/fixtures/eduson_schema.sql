-- Схема SQLite Eduson — дословно legacy/server/src/db.js, migrate().
-- Только DDL, без данных; источник для выдуманных баз в tests/factory.py.

CREATE TABLE IF NOT EXISTS teachers (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  tg TEXT, email TEXT, phone TEXT, telemost TEXT, vk TEXT,
  subjects TEXT,
  role TEXT NOT NULL DEFAULT 'teacher',
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL,
  teacher_id INTEGER,
  name TEXT,
  active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS student_groups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT UNIQUE NOT NULL,
  course TEXT NOT NULL,
  subject_label TEXT,
  teacher_id INTEGER,
  format TEXT NOT NULL DEFAULT 'individual',
  package_size INTEGER,
  lessons_done INTEGER NOT NULL DEFAULT 0,
  lessons_left INTEGER,
  default_time TEXT,
  default_dow INTEGER,
  start_date TEXT,
  amo_url TEXT,
  methodics_url TEXT,
  notes TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS participants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  child_name TEXT, child_tg TEXT, child_phone TEXT, child_email TEXT,
  parent_name TEXT, parent_phone TEXT, parent_tg TEXT, parent_email TEXT,
  age INTEGER, amo_url TEXT, notes TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  group_id INTEGER NOT NULL,
  date TEXT NOT NULL,
  time TEXT NOT NULL,
  duration_min INTEGER NOT NULL DEFAULT 60,
  type TEXT NOT NULL DEFAULT 'regular',
  status TEXT NOT NULL DEFAULT 'scheduled',
  lesson_no INTEGER,
  recording_url TEXT,
  teacher_id INTEGER,
  note TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS lesson_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER UNIQUE,
  group_id INTEGER,
  teacher_id INTEGER,
  lesson_no INTEGER,
  recording_url TEXT,
  took_place TEXT,
  understanding INTEGER,
  interest INTEGER,
  tech_problems TEXT,
  comment TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT, submitted_at TEXT,
  remind_count INTEGER NOT NULL DEFAULT 0,
  last_reminded_at TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  channel TEXT NOT NULL,
  to_addr TEXT,
  subject TEXT, body TEXT,
  session_id INTEGER, group_id INTEGER, teacher_id INTEGER,
  scheduled_for TEXT, sent_at TEXT,
  status TEXT NOT NULL DEFAULT 'queued',
  error TEXT,
  dedupe_key TEXT UNIQUE,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor_id INTEGER, actor_name TEXT,
  action TEXT NOT NULL,
  entity_type TEXT, entity_id INTEGER, code TEXT,
  summary TEXT NOT NULL,
  before_json TEXT, after_json TEXT,
  reverted INTEGER NOT NULL DEFAULT 0,
  revert_of INTEGER
);

CREATE TABLE IF NOT EXISTS renewals (
  group_id INTEGER PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'new',
  note TEXT,
  updated_at TEXT, updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_id ON audit_log(id DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_date ON sessions(date);
CREATE INDEX IF NOT EXISTS idx_sessions_group ON sessions(group_id);
CREATE INDEX IF NOT EXISTS idx_part_group ON participants(group_id);
CREATE INDEX IF NOT EXISTS idx_notif_status ON notifications(status);

-- Колонка, добавленная после первого релиза (db.js, migrate(): ALTER по PRAGMA table_info).
ALTER TABLE student_groups ADD COLUMN schedule_slots TEXT;
