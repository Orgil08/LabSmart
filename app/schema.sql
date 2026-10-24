PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT UNIQUE NOT NULL,
  password TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','viewer')),
  first_name TEXT NOT NULL DEFAULT '',
  last_name TEXT NOT NULL DEFAULT '',
  email TEXT NOT NULL DEFAULT '',
  organization TEXT NOT NULL DEFAULT '',
  user_type TEXT NOT NULL DEFAULT 'student',
  onboarded INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS equipment(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  location TEXT NOT NULL,
  serial TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'available',
  notes TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  action TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS loans(
  id INTEGER PRIMARY KEY,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  borrower TEXT NOT NULL,
  issued_by INTEGER NOT NULL REFERENCES users(id),
  issued_at TEXT NOT NULL,
  due_at TEXT NOT NULL,
  returned_at TEXT,
  return_condition TEXT,
  request_id INTEGER REFERENCES requests(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_loan ON loans(equipment_id) WHERE returned_at IS NULL;
CREATE TABLE IF NOT EXISTS lab_sessions(
  id INTEGER PRIMARY KEY,
  course TEXT NOT NULL,
  lab_no TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  semester_week INTEGER NOT NULL DEFAULT 1,
  planned_students INTEGER NOT NULL DEFAULT 0,
  planned_teams INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'scheduled',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES lab_sessions(id),
  equipment_name TEXT NOT NULL,
  requested_qty INTEGER NOT NULL,
  approved_qty INTEGER NOT NULL DEFAULT 0,
  issued_qty INTEGER NOT NULL DEFAULT 0,
  unmet_qty INTEGER NOT NULL DEFAULT 0,
  requested_by INTEGER NOT NULL REFERENCES users(id),
  recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'pending',
  approved_by INTEGER REFERENCES users(id),
  approved_at TEXT,
  rejection_reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS demand_history(
  id INTEGER PRIMARY KEY,
  course TEXT NOT NULL,
  lab_no TEXT NOT NULL,
  session_date TEXT NOT NULL,
  semester_week INTEGER NOT NULL DEFAULT 1,
  equipment_name TEXT NOT NULL,
  planned_students INTEGER NOT NULL DEFAULT 0,
  planned_teams INTEGER NOT NULL DEFAULT 0,
  units_per_team INTEGER NOT NULL DEFAULT 1,
  shared_units INTEGER NOT NULL DEFAULT 0,
  requested_qty INTEGER NOT NULL,
  forecast_cutoff TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'real'
);
CREATE INDEX IF NOT EXISTS idx_demand_history_type_date ON demand_history(equipment_name, session_date);
CREATE TABLE IF NOT EXISTS model_runs(
  id INTEGER PRIMARY KEY,
  generated_at TEXT NOT NULL,
  forecast_cutoff TEXT NOT NULL,
  data_version TEXT NOT NULL,
  model_name TEXT NOT NULL,
  train_rows INTEGER NOT NULL DEFAULT 0,
  test_rows INTEGER NOT NULL DEFAULT 0,
  mae REAL,
  rmse REAL,
  bias REAL,
  status TEXT NOT NULL DEFAULT 'success',
  note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_model_runs_generated ON model_runs(generated_at DESC);
CREATE TABLE IF NOT EXISTS app_settings(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO app_settings(key,value) VALUES
 ('default_loan_hours','24'),('low_stock_threshold','2'),
 ('prediction_horizon_days','7'),('notifications_enabled','1');
CREATE TABLE IF NOT EXISTS predictions(
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES lab_sessions(id),
  equipment_name TEXT NOT NULL,
  raw_prediction REAL NOT NULL,
  processed_qty INTEGER NOT NULL,
  range_low INTEGER NOT NULL DEFAULT 0,
  range_high INTEGER NOT NULL DEFAULT 0,
  generated_at TEXT NOT NULL,
  forecast_cutoff TEXT NOT NULL,
  horizon_days INTEGER NOT NULL DEFAULT 7,
  method TEXT NOT NULL,
  model_version TEXT NOT NULL,
  data_version TEXT NOT NULL,
  data_points INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_unique ON predictions(session_id,equipment_name,model_version,data_version);
CREATE TABLE IF NOT EXISTS recommendations(
  id INTEGER PRIMARY KEY,
  prediction_id INTEGER NOT NULL REFERENCES predictions(id),
  mandatory_qty INTEGER NOT NULL DEFAULT 0,
  allocation_qty INTEGER NOT NULL DEFAULT 0,
  available_interval INTEGER NOT NULL DEFAULT 0,
  shortage_qty INTEGER NOT NULL DEFAULT 0,
  priority TEXT NOT NULL DEFAULT 'low',
  rule_version TEXT NOT NULL,
  explanation TEXT NOT NULL,
  suggestion TEXT NOT NULL,
  decision_status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS maintenance(
  id INTEGER PRIMARY KEY,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  issue TEXT NOT NULL,
  priority TEXT NOT NULL DEFAULT 'medium',
  scheduled_at TEXT,
  completed_at TEXT,
  outcome TEXT NOT NULL DEFAULT '',
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY,
  user_id INTEGER REFERENCES users(id),
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id INTEGER,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'info',
  entity_type TEXT,
  entity_id INTEGER,
  fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  read_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_user_fp ON notifications(user_id,fingerprint);
CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_loans_due ON loans(due_at,returned_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_created ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications(user_id, read_at, id DESC);

CREATE TABLE IF NOT EXISTS password_reset_requests(
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  status TEXT NOT NULL DEFAULT 'pending',
  source TEXT NOT NULL DEFAULT 'web'
);
CREATE INDEX IF NOT EXISTS idx_password_reset_requested ON password_reset_requests(requested_at DESC);
