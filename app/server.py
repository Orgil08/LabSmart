"""LabSmart local production-style demo server.

Standard-library-only Python HTTP API + SQLite. Existing equipment/users are preserved;
schema migrations add the higher-level laboratory management, lending, reporting and
forecast/recommendation features described by the accompanying project guides.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from email.utils import formatdate
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
DB = Path(os.environ.get("LAB_DB", str(ROOT / "data" / "lab.db")))
SESSIONS: dict[str, dict] = {}
FAILURES: dict[str, list[float]] = {}
REGISTRATIONS: dict[str, list[float]] = {}
STATUSES = ("available", "in_use", "maintenance", "damaged", "lost", "retired")
ROLES = ("admin", "staff", "student")
ROLE_LABELS = {"admin": "Админ", "staff": "Багш / Лабораторийн ажилтан", "student": "Оюутан"}
ROLE_CAPS = {
    "admin": {"manage_equipment", "manage_users", "lending", "requests", "maintenance", "reports", "audit", "ml", "settings"},
    "staff": {"lending", "requests", "maintenance", "reports", "ml", "audit"},
    "student": {"lending", "requests"},
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime | None = None) -> str:
    dt = dt or utc_now()
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def password_hash(value: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), 600_000).hex()
    return salt + ":" + key


def verify_password(value: str, stored: str) -> bool:
    try:
        return hmac.compare_digest(password_hash(value, stored.split(":", 1)[0]), stored)
    except Exception:
        return False


def safe_int(value, default=0, minimum=None, maximum=None):
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None:
        n = max(minimum, n)
    if maximum is not None:
        n = min(maximum, n)
    return n


def clean(value, limit=120, required=False) -> str:
    if not isinstance(value, str):
        raise Error(400, "Оруулсан мэдээллийн төрөл буруу байна.")
    value = value.strip()
    if required and not value:
        raise Error(400, "Заавал бөглөх мэдээлэл дутуу байна.")
    if len(value) > limit:
        raise Error(400, "Оруулсан мэдээлэл хэт урт байна.")
    return value


def migrate_schema(db: sqlite3.Connection):
    """Apply additive migrations while preserving the original tables/data."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(users)").fetchall()}
    additions = [
        ("first_name", "TEXT NOT NULL DEFAULT ''"),
        ("last_name", "TEXT NOT NULL DEFAULT ''"),
        ("email", "TEXT NOT NULL DEFAULT ''"),
        ("organization", "TEXT NOT NULL DEFAULT ''"),
        ("user_type", "TEXT NOT NULL DEFAULT 'student'"),
        ("onboarded", "INTEGER NOT NULL DEFAULT 1"),
    ]
    for name, typ in additions:
        if name not in cols:
            db.execute(f"ALTER TABLE users ADD COLUMN {name} {typ}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email <> ''")

    # Add request/loan workflow metadata without removing the existing tables/data.
    request_cols = {r[1] for r in db.execute("PRAGMA table_info(requests)").fetchall()} if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='requests'").fetchone() else set()
    # requests table itself is created below; these ALTER statements are applied after creation.
    db.execute("UPDATE users SET user_type='admin' WHERE role='admin'")
    db.execute("UPDATE users SET user_type='student' WHERE role='viewer' AND (user_type IS NULL OR user_type='')")
    db.execute("UPDATE users SET email=username || '@labsmart.local' WHERE email=''")

    # The original milestone only allowed available/maintenance/retired. Rebuild the
    # equipment table once so the higher-level workflow can also represent in-use,
    # damaged and lost assets without deleting any existing rows.
    equipment_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='equipment'").fetchone()
    if equipment_sql and "status IN ('available','maintenance','retired')" in equipment_sql[0]:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("DROP TABLE IF EXISTS equipment_new")
        db.execute("""CREATE TABLE equipment_new(
            id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
            location TEXT NOT NULL, serial TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'available',
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("INSERT INTO equipment_new SELECT id,code,name,category,location,serial,status,notes,created_at,updated_at FROM equipment")
        db.execute("DROP TABLE equipment")
        db.execute("ALTER TABLE equipment_new RENAME TO equipment")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_code ON equipment(code)")

    db.execute("""CREATE TABLE IF NOT EXISTS lab_sessions (
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
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY,
        session_id INTEGER NOT NULL REFERENCES lab_sessions(id),
        equipment_name TEXT NOT NULL,
        requested_qty INTEGER NOT NULL,
        approved_qty INTEGER NOT NULL DEFAULT 0,
        issued_qty INTEGER NOT NULL DEFAULT 0,
        unmet_qty INTEGER NOT NULL DEFAULT 0,
        requested_by INTEGER NOT NULL REFERENCES users(id),
        recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        version INTEGER NOT NULL DEFAULT 1
    )""")
    request_cols = {r[1] for r in db.execute("PRAGMA table_info(requests)").fetchall()}
    request_additions = [
        ("status", "TEXT NOT NULL DEFAULT 'pending'"),
        ("approved_by", "INTEGER REFERENCES users(id)"),
        ("approved_at", "TEXT"),
        ("rejection_reason", "TEXT NOT NULL DEFAULT ''"),
    ]
    for name, typ in request_additions:
        if name not in request_cols:
            db.execute(f"ALTER TABLE requests ADD COLUMN {name} {typ}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status, recorded_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_requests_requester ON requests(requested_by, recorded_at)")
    db.execute("""CREATE TABLE IF NOT EXISTS predictions (
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
    )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_unique ON predictions(session_id, equipment_name, model_version, data_version)")
    db.execute("""CREATE TABLE IF NOT EXISTS recommendations (
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
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS maintenance (
        id INTEGER PRIMARY KEY,
        equipment_id INTEGER NOT NULL REFERENCES equipment(id),
        issue TEXT NOT NULL,
        priority TEXT NOT NULL DEFAULT 'medium',
        scheduled_at TEXT,
        completed_at TEXT,
        outcome TEXT NOT NULL DEFAULT '',
        created_by INTEGER NOT NULL REFERENCES users(id),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    loan_cols = {r[1] for r in db.execute("PRAGMA table_info(loans)").fetchall()}
    if "request_id" not in loan_cols:
        db.execute("ALTER TABLE loans ADD COLUMN request_id INTEGER REFERENCES requests(id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_loans_request ON loans(request_id)")

    db.execute("""CREATE TABLE IF NOT EXISTS demand_history (
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
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_demand_history_type_date ON demand_history(equipment_name, session_date)")
    db.execute("""CREATE TABLE IF NOT EXISTS model_runs (
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
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_model_runs_generated ON model_runs(generated_at DESC)")
    db.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""")
    defaults = {
        "default_loan_hours": "24",
        "low_stock_threshold": "2",
        "prediction_horizon_days": "7",
        "notifications_enabled": "1",
    }
    for k,v in defaults.items():
        db.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES(?,?)", (k,v))

    db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY,
        user_id INTEGER REFERENCES users(id),
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id INTEGER,
        detail TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS notifications (
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
    )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_user_fp ON notifications(user_id, fingerprint)")
    db.execute("""CREATE TABLE IF NOT EXISTS password_reset_requests(
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL,
        requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        status TEXT NOT NULL DEFAULT 'pending',
        source TEXT NOT NULL DEFAULT 'web'
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_password_reset_requested ON password_reset_requests(requested_at DESC)")
    db.execute("CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_loans_due ON loans(due_at, returned_at)")
    db.commit()


def log_event(db, user_id, action, entity_type, entity_id=None, detail=""):
    db.execute(
        "INSERT INTO audit_events(user_id,action,entity_type,entity_id,detail) VALUES(?,?,?,?,?)",
        (user_id, action, entity_type, entity_id, detail),
    )


def effective_role(row: sqlite3.Row) -> str:
    if row["role"] == "admin" or row["user_type"] == "admin":
        return "admin"
    return row["user_type"] if row["user_type"] in ("staff", "student") else "student"


def public_user(row: sqlite3.Row) -> dict:
    role = effective_role(row)
    full_name = " ".join(x for x in [row["last_name"], row["first_name"]] if x).strip() or row["username"]
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "organization": row["organization"],
        "role": role,
        "role_label": ROLE_LABELS[role],
        "name": full_name,
        "onboarded": bool(row["onboarded"]),
    }


def initialize(demo=False):
    db = connect()
    try:
        db.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        migrate_schema(db)
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            admin_pw = "LabAdmin2026!" if demo else secrets.token_urlsafe(15)
            viewer_pw = "LabViewer2026!" if demo else secrets.token_urlsafe(15)
            db.execute(
                "INSERT INTO users(username,password,role,first_name,last_name,email,organization,user_type,onboarded) VALUES(?,?,?,?,?,?,?,?,?)",
                ("admin", password_hash(admin_pw), "admin", "Лаб", "Админ", "admin@labsmart.local", "LabSmart лаборатори", "admin", 1),
            )
            db.execute(
                "INSERT INTO users(username,password,role,first_name,last_name,email,organization,user_type,onboarded) VALUES(?,?,?,?,?,?,?,?,?)",
                ("viewer", password_hash(viewer_pw), "viewer", "Оюутан", "Демо", "viewer@labsmart.local", "LabSmart лаборатори", "student", 1),
            )
            print(f"First-run account: admin / {admin_pw}", flush=True)
            print(f"First-run account: viewer / {viewer_pw}", flush=True)
        else:
            db.execute("UPDATE users SET user_type='admin' WHERE role='admin'")

        if demo:
            seed_demo(db)
        db.commit()
        if demo:
            try:
                from ml_pipeline import run_batch
                run_batch(DB)
            except Exception as exc:
                print(f"ML demo batch skipped: {exc}", flush=True)
    finally:
        db.close()


def seed_demo(db):
    samples = [
        ("LAB-001", "Дижитал осциллоскоп", "Хэмжилт", "Лаборатори 201", "SN-001", "available", "2 суваг, сургалтын жишээ"),
        ("LAB-002", "Мультиметр", "Хэмжилт", "Лаборатори 201", "SN-002", "available", "Хүчдэл, гүйдэл хэмжих"),
        ("LAB-003", "Сүлжээний чиглүүлэгч", "Сүлжээ", "Лаборатори 302", "SN-003", "maintenance", "Портын оношилгоонд"),
        ("LAB-004", "Микроскоп", "Оптик", "Лаборатори 203", "SN-004", "available", "Сургалтын зориулалттай"),
        ("LAB-005", "Лабораторийн тэжээлийн блок", "Электроник", "Лаборатори 201", "SN-005", "available", "0–30V"),
        ("LAB-006", "Сүлжээний коммутатор", "Сүлжээ", "Лаборатори 302", "SN-006", "retired", "Ашиглалтаас хассан жишээ"),
    ]
    if db.execute("SELECT COUNT(*) FROM equipment").fetchone()[0] == 0:
        db.executemany(
            "INSERT INTO equipment(code,name,category,location,serial,status,notes) VALUES(?,?,?,?,?,?,?)",
            samples,
        )

    if db.execute("SELECT COUNT(*) FROM lab_sessions").fetchone()[0] == 0:
        base = utc_now().replace(hour=2, minute=0, second=0, microsecond=0)
        session_rows = []
        courses = [
            ("Embedded Systems", "LAB-01", 24, 8),
            ("Computer Networks", "LAB-02", 21, 7),
            ("Electronics Practice", "LAB-03", 18, 6),
            ("Physics Lab", "LAB-04", 12, 4),
        ]
        for i in range(8):
            start = base + timedelta(days=i + 1, hours=(i % 3) * 2)
            end = start + timedelta(hours=2)
            c, lab, students, teams = courses[i % len(courses)]
            session_rows.append((c, lab, iso_utc(start), iso_utc(end), 8 + i, students, teams, "scheduled"))
        db.executemany(
            "INSERT INTO lab_sessions(course,lab_no,start_at,end_at,semester_week,planned_students,planned_teams,status) VALUES(?,?,?,?,?,?,?,?)",
            session_rows,
        )

    if db.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0:
        sessions = db.execute("SELECT id,course,planned_teams FROM lab_sessions ORDER BY id LIMIT 8").fetchall()
        uid = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
        for i, s in enumerate(sessions):
            for name, base_qty in [("Дижитал осциллоскоп", 5), ("Мультиметр", 6), ("Лабораторийн тэжээлийн блок", 4)]:
                qty = max(1, base_qty + ((s[0] + len(name)) % 4) - 1)
                db.execute(
                    "INSERT INTO requests(session_id,equipment_name,requested_qty,approved_qty,issued_qty,unmet_qty,requested_by) VALUES(?,?,?,?,?,?,?)",
                    (s[0], name, qty, qty, min(qty, 4 if name == "Мультиметр" else qty), max(0, qty - min(qty, 4 if name == "Мультиметр" else qty)), uid),
                )

    if db.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0:
        now = iso_utc()
        cutoff = iso_utc(utc_now().replace(hour=1, minute=0, second=0, microsecond=0))
        sessions = db.execute("SELECT id FROM lab_sessions ORDER BY id LIMIT 4").fetchall()
        for s_idx, s in enumerate(sessions):
            for name, raw in [("Дижитал осциллоскоп", 3.4 + s_idx * 0.4), ("Мультиметр", 4.8 + s_idx * 0.6), ("Лабораторийн тэжээлийн блок", 3.3 + s_idx * 0.8)]:
                processed = max(0, int(raw + 0.999))
                low, high = max(0, processed - 1), processed + 1
                cur = db.execute(
                    "INSERT OR IGNORE INTO predictions(session_id,equipment_name,raw_prediction,processed_qty,range_low,range_high,generated_at,forecast_cutoff,horizon_days,method,model_version,data_version,data_points) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s[0], name, raw, processed, low, high, now, cutoff, 7, "rule_fallback" if s_idx == 0 else "Random Forest Regressor", "demo-v1", "demo-2026-10", 8 + s_idx),
                )
                pid = cur.lastrowid or db.execute("SELECT id FROM predictions WHERE session_id=? AND equipment_name=?", (s[0], name)).fetchone()[0]
                available = db.execute("SELECT COUNT(*) FROM equipment WHERE name=? AND status='available'", (name,)).fetchone()[0]
                shortage = max(0, processed - available)
                priority = "critical" if shortage >= 3 else "high" if shortage > 0 else "low"
                suggestion = (
                    f"Нэмэлт {shortage}–{max(shortage, shortage + 2)} ширхэг бэлдэх эсвэл өөр ээлжийн сул нөөцийг шалгаарай."
                    if shortage > 0 else "Одоогийн нөөцөөр хангах боломжтой. Хуваарь ойртоход дахин шалгаарай."
                )
                explanation = (
                    f"Ирэх session-ийн төлөвлөсөн хэрэгцээ {processed} ширхэг, тухайн интервалын бэлэн нөөц {available} байна."
                )
                db.execute(
                    "INSERT OR IGNORE INTO recommendations(prediction_id,mandatory_qty,allocation_qty,available_interval,shortage_qty,priority,rule_version,explanation,suggestion,decision_status) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (pid, 0, processed, available, shortage, priority, "interval-v1", explanation, suggestion, "open"),
                )

    if db.execute("SELECT COUNT(*) FROM maintenance").fetchone()[0] == 0:
        equip = db.execute("SELECT id FROM equipment WHERE status='maintenance' LIMIT 1").fetchone()
        if equip:
            admin_id = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()[0]
            db.execute("INSERT INTO maintenance(equipment_id,issue,priority,scheduled_at,created_by) VALUES(?,?,?,?,?)", (equip[0], "Сүлжээний портын оношилгоо", "high", iso_utc(utc_now() + timedelta(days=1)), admin_id))

    # Synthetic historical demand is for pipeline/demo validation only, never as proof
    # of production accuracy. The ML guide explicitly distinguishes this from real data.
    if db.execute("SELECT COUNT(*) FROM demand_history").fetchone()[0] == 0:
        names = ["Дижитал осциллоскоп", "Мультиметр", "Лабораторийн тэжээлийн блок"]
        courses = [
            ("Embedded Systems", "LAB-01"), ("Computer Networks", "LAB-02"),
            ("Electronics Practice", "LAB-03"), ("Physics Lab", "LAB-04")
        ]
        base = utc_now().date() - timedelta(days=70)
        rows = []
        for i in range(28):
            d = base + timedelta(days=i * 2)
            week = min(12, 1 + i // 3)
            for t_idx, name in enumerate(names):
                c, lab = courses[(i + t_idx) % len(courses)]
                students = 12 + ((i * 3 + t_idx * 4) % 18)
                teams = max(2, (students + 2) // 3)
                units = 1
                shared = 0 if t_idx == 0 else (1 if t_idx == 2 else 0)
                noise = ((i * 7 + t_idx * 3) % 5) - 2
                demand = max(1, teams * units + shared + noise + (1 if t_idx == 1 and i % 4 == 0 else 0))
                cutoff = iso_utc(datetime(d.year, d.month, d.day, 8, 0, tzinfo=timezone.utc))
                rows.append((c, lab, d.isoformat(), week, name, students, teams, units, shared, demand, cutoff, "synthetic_demo"))
        db.executemany(
            "INSERT INTO demand_history(course,lab_no,session_date,semester_week,equipment_name,planned_students,planned_teams,units_per_team,shared_units,requested_qty,forecast_cutoff,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )

    # Add completed demo usage history only; do not change the existing equipment
    # statuses supplied by the original project.
    if db.execute("SELECT COUNT(*) FROM loans").fetchone()[0] == 0:
        viewer = db.execute("SELECT id,username FROM users WHERE username='viewer' LIMIT 1").fetchone()
        equip = db.execute("SELECT id FROM equipment WHERE status='available' ORDER BY id LIMIT 1").fetchone()
        if viewer and equip:
            for days_ago in (1, 3, 5):
                issued = utc_now() - timedelta(days=days_ago, hours=2)
                due = issued + timedelta(hours=4)
                returned = issued + timedelta(hours=3)
                db.execute("INSERT INTO loans(equipment_id,borrower,issued_by,issued_at,due_at,returned_at,return_condition) VALUES(?,?,?,?,?,?,?)", (equip[0], viewer[1], viewer[0], iso_utc(issued), iso_utc(due), iso_utc(returned), 'Сайн'))


def next_username(db, email: str) -> str:
    base = re.sub(r"[^a-z0-9_.-]", "", email.lower().split("@", 1)[0])[:32] or "user"
    candidate = base
    i = 1
    while db.execute("SELECT 1 FROM users WHERE username=? COLLATE NOCASE", (candidate,)).fetchone():
        i += 1
        candidate = f"{base}{i}"
    return candidate


class Error(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message


class Handler(BaseHTTPRequestHandler):
    server_version = "LabSmart/2.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, value, cookie=None, content_type="application/json; charset=utf-8", download=False):
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Permissions-Policy", "camera=(self)")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="labsmart-report.csv"')
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(data)

    def json_body(self):
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 0 or size > 100_000:
                raise Error(413, "Өгөгдөл хэт том байна.")
            raw = self.rfile.read(size)
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (ValueError, json.JSONDecodeError):
            raise Error(400, "Оруулсан мэдээлэл буруу байна.")

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return origin in {f"http://{host}", f"https://{host}"}

    def session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except Exception:
            raise Error(401, "Нэвтэрнэ үү.")
        token = cookie["session"].value if "session" in cookie else ""
        item = SESSIONS.get(token)
        if not item or item["expires"] < time.time():
            SESSIONS.pop(token, None)
            raise Error(401, "Таны нэвтрэх хугацаа дууссан байна. Дахин нэвтэрнэ үү.")
        with connect() as db:
            row = db.execute("SELECT * FROM users WHERE id=?", (item["id"],)).fetchone()
        if not row:
            raise Error(401, "Хэрэглэгчийн бүртгэл олдсонгүй.")
        item["role"] = effective_role(row)
        item["user"] = row
        return token, item

    def require(self, capability: str):
        _, s = self.session()
        if capability not in ROLE_CAPS.get(s["role"], set()):
            raise Error(403, "Танд энэ үйлдлийг хийх эрх байхгүй.")
        return s

    def csrf(self, s):
        if not hmac.compare_digest(self.headers.get("X-CSRF-Token", ""), s["csrf"]):
            raise Error(403, "Аюулгүй байдлын баталгаажуулалт буруу байна. Хуудсаа дахин ачаална уу.")

    def dispatch(self):
        path = urlsplit(self.path).path
        if self.command == "GET" and path in ("/", "/app.js", "/style.css"):
            name = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}[path]
            typ = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
            return self.send(200, (ROOT / "static" / name).read_bytes(), content_type=f"{typ}; charset=utf-8")

        # Public catalog for the welcome page.
        if self.command == "GET" and path == "/api/public/equipment":
            q = parse_qs(urlsplit(self.path).query)
            term = q.get("q", [""])[0].casefold().strip()
            with connect() as db:
                rows = [dict(r) for r in db.execute("SELECT id,code,name,category,location,status FROM equipment WHERE status <> 'retired' ORDER BY name,code")]
            if term:
                rows = [r for r in rows if term in " ".join(str(r[k]) for k in ("code", "name", "category", "location")).casefold()]
            return self.send(200, rows)

        if path == "/api/password-reset" and self.command == "POST":
            if not self.origin_ok():
                raise Error(403, "Хүсэлтийн эх сурвалж буруу байна.")
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise Error(415, "JSON өгөгдөл шаардлагатай.")
            return self.password_reset_request()

        if path in ("/api/login", "/api/register") and self.command == "POST":
            if not self.origin_ok():
                raise Error(403, "Хүсэлтийн эх сурвалж буруу байна.")
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise Error(415, "JSON өгөгдөл шаардлагатай.")
            data = self.json_body()
            if path == "/api/register":
                return self.register(data)
            return self.login(data)

        token, s = self.session()
        if self.command != "GET":
            self.csrf(s)
        if path == "/api/logout" and self.command == "POST":
            SESSIONS.pop(token, None)
            secure = "; Secure" if os.getenv("LAB_SECURE_COOKIE") == "1" else ""
            return self.send(200, {"ok": True}, f"session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/{secure}")
        if path == "/api/me" and self.command == "GET":
            return self.send(200, {**public_user(s["user"]), "csrf": s["csrf"]})
        if path == "/api/profile" and self.command in ("GET", "PUT"):
            if self.command == "GET":
                return self.send(200, public_user(s["user"]))
            return self.profile_update(s)
        if path == "/api/dashboard" and self.command == "GET":
            return self.dashboard(s)
        if path == "/api/equipment" and self.command == "GET":
            return self.list_equipment()
        if path == "/api/equipment" and self.command == "POST":
            self.require("manage_equipment")
            return self.create_equipment(s)
        if path.startswith("/api/equipment/") and path.endswith("/history") and self.command == "GET":
            eid = self.path_id(path, suffix="/history")
            return self.equipment_history(eid)
        if path.startswith("/api/equipment/") and self.command == "PUT":
            self.require("manage_equipment")
            eid = self.path_id(path)
            return self.update_equipment(s, eid)
        if path.startswith("/api/equipment/") and self.command == "GET":
            eid = self.path_id(path)
            return self.equipment_detail(eid)
        if path == "/api/loans" and self.command == "GET":
            return self.list_loans(s)
        if path == "/api/loans" and self.command == "POST":
            self.require("lending")
            return self.create_loan(s)
        if path.startswith("/api/loans/") and path.endswith("/return") and self.command == "POST":
            lid = self.path_id(path, suffix="/return")
            return self.return_loan(s, lid)
        if path == "/api/requests" and self.command == "GET":
            self.require("requests")
            return self.list_requests(s)
        if path == "/api/requests" and self.command == "POST":
            self.require("requests")
            return self.create_request(s)
        if path.startswith("/api/requests/") and path.endswith("/status") and self.command == "POST":
            self.require("requests")
            rid = self.path_id(path, suffix="/status")
            return self.update_request_status(s, rid)
        if path == "/api/sessions" and self.command == "GET":
            self.require("requests")
            return self.list_sessions()
        if path == "/api/predictions" and self.command == "GET":
            self.require("ml")
            return self.predictions()
        if path == "/api/recommendations" and self.command == "GET":
            self.require("ml")
            return self.recommendations()
        if path.startswith("/api/recommendations/") and path.endswith("/status") and self.command == "POST":
            self.require("ml")
            rid = self.path_id(path, suffix="/status")
            return self.update_recommendation_status(s, rid)
        if path == "/api/reports" and self.command == "GET":
            self.require("reports")
            return self.reports()
        if path == "/api/reports/export" and self.command == "GET":
            self.require("reports")
            return self.report_export()
        if path == "/api/audit" and self.command == "GET":
            self.require("audit")
            return self.audit_all()
        if path.startswith("/api/audit/") and self.command == "GET":
            eid = self.path_id(path)
            return self.equipment_history(eid)
        if path == "/api/maintenance" and self.command == "GET":
            self.require("maintenance")
            return self.list_maintenance()
        if path == "/api/maintenance" and self.command == "POST":
            self.require("maintenance")
            return self.create_maintenance(s)
        if path.startswith("/api/maintenance/") and self.command == "PUT":
            self.require("maintenance")
            mid = self.path_id(path)
            return self.update_maintenance(s, mid)
        if path == "/api/notifications" and self.command == "GET":
            return self.notifications(s)
        if path.startswith("/api/notifications/") and path.endswith("/read") and self.command == "POST":
            nid = self.path_id(path, suffix="/read")
            return self.mark_notification(s, nid)
        if path == "/api/notifications/read-all" and self.command == "POST":
            return self.mark_all_notifications(s)
        if path == "/api/users" and self.command == "GET":
            self.require("manage_users")
            return self.users_list()
        if path.startswith("/api/users/") and path.endswith("/role") and self.command == "POST":
            self.require("manage_users")
            uid = self.path_id(path, suffix="/role")
            return self.update_user_role(s, uid)
        if path == "/api/settings" and self.command == "GET":
            self.require("settings")
            return self.settings_get()
        if path == "/api/settings" and self.command == "PUT":
            self.require("settings")
            return self.settings_update(s)
        if path == "/api/ml/run" and self.command == "POST":
            self.require("ml")
            return self.ml_run(s)
        if path == "/api/ml/summary" and self.command == "GET":
            self.require("ml")
            return self.ml_summary()
        raise Error(404, "Хүссэн хуудас эсвэл үйлчилгээ олдсонгүй.")

    def password_reset_request(self):
        data = self.json_body()
        email = clean(data.get("email"), 160, True).lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise Error(400, "И-мэйл хаяг буруу байна.")
        with connect() as db:
            exists = db.execute("SELECT id FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone()
            db.execute("INSERT INTO password_reset_requests(email,status) VALUES(?,?)", (email, "pending"))
            # Keep the public response generic, while preserving an auditable admin-side record.
            log_event(db, None, "Нууц үг сэргээх хүсэлт", "security", None, "email_registered=" + ("1" if exists else "0"))
            if exists:
                admins = [r["id"] for r in db.execute("SELECT id FROM users WHERE role='admin'").fetchall()]
                fp_base = f"password-reset:{email}:{datetime.now(timezone.utc).date()}"
                for admin_id in admins:
                    db.execute(
                        "INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint,entity_type) VALUES(?,?,?,?,?,?,?)",
                        (admin_id, "security", "Нууц үг сэргээх хүсэлт", "Хэрэглэгч нууц үгээ сэргээх хүсэлт илгээлээ. Захиргааны сувгаар баталгаажуулна уу.", "info", fp_base + f":{admin_id}", "security"),
                    )
        return self.send(200, {"ok": True, "message": "Хэрэв энэ и-мэйл бүртгэлтэй бол сэргээх дараагийн алхмыг админ баталгаажуулна."})

    def register(self, data):
        first_name = clean(data.get("first_name"), 80, True)
        last_name = clean(data.get("last_name"), 80, True)
        email = clean(data.get("email"), 160, True).lower()
        organization = clean(data.get("organization"), 160, True)
        password = data.get("password")
        confirm = data.get("confirm")
        role = data.get("user_type") or "student"
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise Error(400, "И-мэйл хаяг буруу байна.")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise Error(400, "Нууц үг 8–128 тэмдэгттэй байна.")
        if password != confirm:
            raise Error(400, "Нууц үгүүд тохирохгүй байна.")
        if role not in ("student", "staff"):
            raise Error(400, "Хэрэглэгчийн төрөл буруу байна.")
        ip = self.client_address[0]
        now = time.time()
        attempts = [t for t in REGISTRATIONS.get(ip, []) if t > now - 60]
        if len(attempts) >= 10:
            raise Error(429, "Түр хүлээгээд дахин оролдоно уу.")
        attempts.append(now)
        REGISTRATIONS[ip] = attempts
        with connect() as db:
            if db.execute("SELECT 1 FROM users WHERE email=? COLLATE NOCASE", (email,)).fetchone():
                raise Error(409, "Энэ и-мэйл бүртгэлтэй байна.")
            username = next_username(db, email)
            db.execute(
                "INSERT INTO users(username,password,role,first_name,last_name,email,organization,user_type,onboarded) VALUES(?,?,?,?,?,?,?,?,0)",
                (username, password_hash(password), "viewer", first_name, last_name, email, organization, role),
            )
        return self.send(201, {"ok": True, "username": username, "message": "Бүртгэл амжилттай үүслээ."})

    def login(self, data):
        identity = data.get("email") or data.get("username")
        password = data.get("password")
        if not isinstance(identity, str) or not isinstance(password, str) or len(password) > 256:
            raise Error(400, "Нэвтрэх мэдээллээ шалгана уу.")
        ip = self.client_address[0]
        now = time.time()
        attempts = [t for t in FAILURES.get(ip, []) if t > now - 60]
        if len(attempts) >= 10:
            raise Error(429, "Түр хүлээгээд дахин оролдоно уу.")
        FAILURES[ip] = attempts
        with connect() as db:
            user = db.execute("SELECT * FROM users WHERE email=? COLLATE NOCASE OR username=? COLLATE NOCASE", (identity.strip(), identity.strip())).fetchone()
        if not user or not verify_password(password, user["password"]):
            attempts.append(now)
            raise Error(401, "И-мэйл эсвэл нууц үг буруу байна.")
        FAILURES.pop(ip, None)
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        SESSIONS[token] = {"id": user["id"], "csrf": csrf, "expires": now + 28_800, "role": effective_role(user), "user": user}
        secure = "; Secure" if os.getenv("LAB_SECURE_COOKIE") == "1" else ""
        cookie = f"session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800{secure}"
        with connect() as db:
            log_event(db, user["id"], "Нэвтэрсэн", "auth", user["id"], "web login")
        return self.send(200, {**public_user(user), "csrf": csrf}, cookie)

    def profile_update(self, s):
        data = self.json_body()
        first_name = clean(data.get("first_name"), 80, True)
        last_name = clean(data.get("last_name"), 80, True)
        organization = clean(data.get("organization"), 160, True)
        onboarding = 1 if bool(data.get("onboarded", False)) else None
        with connect() as db:
            if onboarding is None:
                db.execute("UPDATE users SET first_name=?,last_name=?,organization=? WHERE id=?", (first_name, last_name, organization, s["id"]))
            else:
                db.execute("UPDATE users SET first_name=?,last_name=?,organization=?,onboarded=? WHERE id=?", (first_name, last_name, organization, onboarding, s["id"]))
            log_event(db, s["id"], "Профайл шинэчилсэн", "user", s["id"], "profile")
            row = db.execute("SELECT * FROM users WHERE id=?", (s["id"],)).fetchone()
        s["user"] = row
        return self.send(200, public_user(row))

    def list_equipment(self):
        q = parse_qs(urlsplit(self.path).query)
        term = q.get("q", [""])[0].casefold().strip()
        category = q.get("category", [""])[0]
        location = q.get("location", [""])[0]
        status = q.get("status", [""])[0]
        available_only = q.get("available", [""])[0] == "1"
        with connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM equipment ORDER BY id DESC")]
            open_loans = {r["equipment_id"]: dict(r) for r in db.execute("SELECT id,equipment_id,borrower,issued_at,due_at FROM loans WHERE returned_at IS NULL")}
        for r in rows:
            r["display_status"] = {"available": "available", "in_use": "in_use", "maintenance": "maintenance", "damaged": "damaged", "lost": "lost", "retired": "retired"}.get(r["status"], r["status"])
            r["current_borrower"] = open_loans.get(r["id"], {}).get("borrower")
            r["loan_id"] = open_loans.get(r["id"], {}).get("id")
            r["available"] = r["status"] == "available"
        if term:
            rows = [r for r in rows if term in " ".join(str(r[k]) for k in ("code", "name", "serial", "category", "location")).casefold()]
        if category:
            rows = [r for r in rows if r["category"] == category]
        if location:
            rows = [r for r in rows if r["location"] == location]
        if status:
            rows = [r for r in rows if r["status"] == status]
        if available_only:
            rows = [r for r in rows if r["available"]]
        return self.send(200, rows)

    def equipment_detail(self, eid):
        with connect() as db:
            item = db.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone()
            if not item:
                raise Error(404, "Төхөөрөмж олдсонгүй.")
            loan = db.execute("SELECT id,borrower,issued_at,due_at,return_condition FROM loans WHERE equipment_id=? AND returned_at IS NULL ORDER BY id DESC LIMIT 1", (eid,)).fetchone()
            use_count = db.execute("SELECT COUNT(*) FROM loans WHERE equipment_id=?", (eid,)).fetchone()[0]
            maintenance_count = db.execute("SELECT COUNT(*) FROM maintenance WHERE equipment_id=?", (eid,)).fetchone()[0]
        return self.send(200, {**dict(item), "current_loan": dict(loan) if loan else None, "usage_count": use_count, "maintenance_count": maintenance_count})

    def create_equipment(self, s):
        data = self.json_body()
        code = clean(data.get("code"), 120, True).upper()
        name = clean(data.get("name"), 120, True)
        category = clean(data.get("category"), 120, True)
        location = clean(data.get("location"), 120, True)
        serial = clean(data.get("serial", ""), 120)
        status = data.get("status", "available")
        notes = clean(data.get("notes", ""), 1000)
        if status not in STATUSES:
            raise Error(400, "Төлөв буруу байна.")
        with connect() as db:
            cur = db.execute("INSERT INTO equipment(code,name,category,location,serial,status,notes,updated_at) VALUES(?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", (code, name, category, location, serial, status, notes))
            eid = cur.lastrowid
            db.execute("INSERT INTO audit(equipment_id,user_id,action,detail) VALUES(?,?,?,?)", (eid, s["id"], "Нэмсэн", json.dumps({"code": code, "name": name}, ensure_ascii=False)))
            log_event(db, s["id"], "Төхөөрөмж нэмсэн", "equipment", eid, code)
        return self.send(201, {"id": eid})

    def update_equipment(self, s, eid):
        data = self.json_body()
        code = clean(data.get("code"), 120, True).upper()
        name = clean(data.get("name"), 120, True)
        category = clean(data.get("category"), 120, True)
        location = clean(data.get("location"), 120, True)
        serial = clean(data.get("serial", ""), 120)
        status = data.get("status", "available")
        notes = clean(data.get("notes", ""), 1000)
        if status not in STATUSES:
            raise Error(400, "Төлөв буруу байна.")
        with connect() as db:
            current = db.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone()
            if not current:
                raise Error(404, "Төхөөрөмж олдсонгүй.")
            db.execute("UPDATE equipment SET code=?,name=?,category=?,location=?,serial=?,status=?,notes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (code, name, category, location, serial, status, notes, eid))
            db.execute("INSERT INTO audit(equipment_id,user_id,action,detail) VALUES(?,?,?,?)", (eid, s["id"], "Зассан", json.dumps({"code": code, "name": name, "status": status}, ensure_ascii=False)))
            log_event(db, s["id"], "Төхөөрөмж шинэчилсэн", "equipment", eid, code)
        return self.send(200, {"id": eid})

    def path_id(self, path, suffix=""):
        raw = path[len("/api/") :]
        if suffix:
            raw = raw[: -len(suffix)]
        raw = raw.rstrip("/").split("/")[-1]
        try:
            return int(raw)
        except ValueError:
            raise Error(400, "Дугаар буруу байна.")

    def equipment_history(self, eid):
        with connect() as db:
            rows = [dict(r) for r in db.execute("SELECT a.action,a.detail,a.created_at,u.username FROM audit a JOIN users u ON u.id=a.user_id WHERE a.equipment_id=? ORDER BY a.id DESC", (eid,))]
            events = [dict(r) for r in db.execute("SELECT e.action,e.detail,e.created_at,COALESCE(u.username,'system') username FROM audit_events e LEFT JOIN users u ON u.id=e.user_id WHERE e.entity_type='equipment' AND e.entity_id=? ORDER BY e.id DESC", (eid,))]
        return self.send(200, {"changes": rows, "events": events})

    def create_loan(self, s):
        data = self.json_body()
        eid = safe_int(data.get("equipment_id"), -1, 1)
        due_at = clean(data.get("due_at"), 64, True)
        request_id = safe_int(data.get("request_id"), 0, 0)
        try:
            due_dt = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        except ValueError:
            raise Error(400, "Буцаах хугацааг зөв оруулна уу.")
        if due_dt <= utc_now():
            raise Error(400, "Буцаах хугацаа ирээдүйд байна.")
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            item = db.execute("SELECT * FROM equipment WHERE id=?", (eid,)).fetchone()
            if not item:
                raise Error(404, "Төхөөрөмж олдсонгүй.")
            if item["status"] != "available":
                raise Error(409, "Энэ төхөөрөмж одоогоор авах боломжгүй байна.")
            if db.execute("SELECT 1 FROM loans WHERE equipment_id=? AND returned_at IS NULL", (eid,)).fetchone():
                raise Error(409, "Энэ төхөөрөмж аль хэдийн олгогдсон байна.")
            request = None
            if request_id:
                request = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
                if not request:
                    raise Error(404, "Хүсэлтийн бүртгэл олдсонгүй.")
                if request["requested_by"] != s["id"]:
                    raise Error(403, "Та зөвхөн өөрийн хүсэлтийг ашиглана.")
                if request["status"] != "approved":
                    raise Error(409, "Энэ хүсэлт батлагдаагүй байна.")
                if request["issued_qty"] >= max(1, request["approved_qty"]):
                    raise Error(409, "Энэ хүсэлтийн зөвшөөрөгдсөн хэмжээг ашигласан байна.")
            borrower = " ".join(x for x in [s["user"]["last_name"], s["user"]["first_name"]] if x).strip() or s["user"]["username"]
            cur = db.execute("INSERT INTO loans(equipment_id,borrower,issued_by,issued_at,due_at,request_id) VALUES(?,?,?,?,?,?)", (eid, borrower, s["id"], iso_utc(), iso_utc(due_dt), request_id or None))
            lid = cur.lastrowid
            db.execute("UPDATE equipment SET status='in_use',updated_at=CURRENT_TIMESTAMP WHERE id=?", (eid,))
            if request_id:
                db.execute("UPDATE requests SET issued_qty=issued_qty+1, unmet_qty=MAX(0,requested_qty-(approved_qty)), version=version+1 WHERE id=?", (request_id,))
            db.execute("INSERT INTO audit(equipment_id,user_id,action,detail) VALUES(?,?,?,?)", (eid, s["id"], "Олгосон", f"loan:{lid}"))
            log_event(db, s["id"], "Төхөөрөмж олгосон", "loan", lid, f"equipment:{eid}")
        return self.send(201, {"id": lid, "message": "Төхөөрөмж амжилттай бүртгэгдлээ."})

    def list_loans(self, s):
        q = parse_qs(urlsplit(self.path).query)
        mine = q.get("mine", [""])[0] == "1"
        with connect() as db:
            sql = """SELECT l.*, e.code,e.name,e.category,e.location,e.status,
                     CASE WHEN l.returned_at IS NULL AND l.due_at < ? THEN 1 ELSE 0 END overdue
                     FROM loans l JOIN equipment e ON e.id=l.equipment_id"""
            params = [iso_utc()]
            if mine or s["role"] == "student":
                sql += " WHERE l.issued_by=? OR lower(l.borrower) LIKE ?"
                params.extend([s["id"], f"%{(s['user']['first_name'] or s['user']['username']).lower()}%"]) 
            sql += " ORDER BY l.id DESC"
            rows = [dict(r) for r in db.execute(sql, params)]
        return self.send(200, rows)

    def return_loan(self, s, lid):
        data = self.json_body()
        condition = clean(data.get("return_condition", "Сайн"), 160)
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            loan = db.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
            if not loan:
                raise Error(404, "Олголтын бүртгэл олдсонгүй.")
            if loan["returned_at"]:
                raise Error(409, "Энэ төхөөрөмжийг өмнө нь буцаасан байна.")
            if s["role"] == "student" and loan["issued_by"] != s["id"]:
                raise Error(403, "Та зөвхөн өөрийн авсан төхөөрөмжийг буцаана.")
            new_status = "maintenance" if any(w in condition.casefold() for w in ("гэмт", "эвдэр", "засвар")) else "available"
            db.execute("UPDATE loans SET returned_at=?,return_condition=? WHERE id=?", (iso_utc(), condition, lid))
            db.execute("UPDATE equipment SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, loan["equipment_id"]))
            db.execute("INSERT INTO audit(equipment_id,user_id,action,detail) VALUES(?,?,?,?)", (loan["equipment_id"], s["id"], "Буцаасан", condition))
            log_event(db, s["id"], "Төхөөрөмж буцаасан", "loan", lid, condition)
        return self.send(200, {"ok": True, "message": "Төхөөрөмж амжилттай бүртгэгдлээ."})

    def list_sessions(self):
        with connect() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM lab_sessions ORDER BY start_at")]
        return self.send(200, rows)

    def list_requests(self, s):
        with connect() as db:
            sql = """SELECT r.*,s.course,s.lab_no,s.start_at,s.end_at,
                     COALESCE(NULLIF(TRIM(u.first_name||' '||u.last_name),''),u.username) requester
                     FROM requests r JOIN lab_sessions s ON s.id=r.session_id
                     JOIN users u ON u.id=r.requested_by"""
            params = []
            if s["role"] == "student":
                sql += " WHERE r.requested_by=?"
                params.append(s["id"])
            sql += " ORDER BY CASE r.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,s.start_at,r.id DESC"
            rows = [dict(r) for r in db.execute(sql, params)]
            for r in rows:
                r["available_now"] = db.execute("SELECT COUNT(*) FROM equipment WHERE name=? AND status='available'", (r["equipment_name"],)).fetchone()[0]
        return self.send(200, rows)

    def create_request(self, s):
        data = self.json_body()
        session_id = safe_int(data.get("session_id"), -1, 1)
        name = clean(data.get("equipment_name"), 120, True)
        qty = safe_int(data.get("requested_qty"), 0, 1, 500)
        with connect() as db:
            session = db.execute("SELECT * FROM lab_sessions WHERE id=?", (session_id,)).fetchone()
            if not session:
                raise Error(404, "Хичээлийн session олдсонгүй.")
            if datetime.fromisoformat(session["start_at"].replace("Z", "+00:00")) <= utc_now():
                raise Error(400, "Зөвхөн ирээдүйн хичээлд хүсэлт гаргана.")
            duplicate = db.execute("SELECT id FROM requests WHERE session_id=? AND equipment_name=? AND requested_by=? AND status='pending'", (session_id, name, s["id"])).fetchone()
            if duplicate:
                raise Error(409, "Энэ төхөөрөмжийн хүсэлт аль хэдийн хүлээгдэж байна.")
            db.execute("INSERT INTO requests(session_id,equipment_name,requested_qty,approved_qty,requested_by,status) VALUES(?,?,?,?,?,?)", (session_id, name, qty, 0, s["id"], "pending"))
            rid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            log_event(db, s["id"], "Төхөөрөмж хүссэн", "request", rid, f"{name} × {qty}")
        return self.send(201, {"id": rid, "message": "Хүсэлт амжилттай илгээгдлээ."})

    def update_request_status(self, s, rid):
        if s["role"] not in ("admin", "staff"):
            raise Error(403, "Танд хүсэлтийг шийдвэрлэх эрх байхгүй байна.")
        data = self.json_body()
        status = data.get("status")
        if status not in ("approved", "rejected", "pending"):
            raise Error(400, "Хүсэлтийн төлөв буруу байна.")
        if status == "pending":
            raise Error(400, "Хүсэлтийг буцаан хүлээлгийн төлөвт оруулахгүй.")
        with connect() as db:
            req = db.execute("SELECT r.*,u.first_name,u.last_name FROM requests r JOIN users u ON u.id=r.requested_by WHERE r.id=?", (rid,)).fetchone()
            if not req:
                raise Error(404, "Төхөөрөмжийн хүсэлт олдсонгүй.")
            if req["status"] != "pending":
                raise Error(409, "Энэ хүсэлтийн төлөв аль хэдийн шийдэгдсэн байна.")
            approved_qty = safe_int(data.get("approved_qty"), req["requested_qty"], 0, req["requested_qty"]) if status == "approved" else 0
            reason = clean(data.get("reason", ""), 300)
            if status == "rejected" and not reason:
                reason = "Энэ хүсэлтийг одоогоор батлах боломжгүй байна."
            now = iso_utc()
            db.execute("UPDATE requests SET status=?,approved_qty=?,approved_by=?,approved_at=?,rejection_reason=?,version=version+1 WHERE id=?", (status, approved_qty, s["id"], now, reason, rid))
            label = "баталлаа" if status == "approved" else "татгалзлаа"
            log_event(db, s["id"], f"Төхөөрөмжийн хүсэлт {label}", "request", rid, f"{req['equipment_name']} × {approved_qty or req['requested_qty']}")
            name = " ".join(x for x in (req["first_name"], req["last_name"]) if x).strip() or "хэрэглэгч"
            title = "Төхөөрөмжийн хүсэлт батлагдлаа" if status == "approved" else "Төхөөрөмжийн хүсэлт татгалзлаа"
            message = (f"{req['equipment_name']} × {approved_qty} хүсэлт батлагдлаа. QR-аар авах боломжтой." if status == "approved"
                       else f"{req['equipment_name']} хүсэлтийг одоогоор батлахгүй байна. {reason}")
            fp = f"request:{rid}:{status}:{req['version']+1}"
            db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint,entity_type,entity_id) VALUES(?,?,?,?,?,?,?,?)", (req["requested_by"], "request", title, message, "info" if status == "approved" else "warning", fp, "request", rid))
        return self.send(200, {"ok": True, "message": "Хүсэлтийн төлөв шинэчлэгдлээ."})

    def ml_run(self, s):
        try:
            from ml_pipeline import run_batch
            summary = run_batch(DB)
            with connect() as db:
                log_event(db, s["id"], "Хэрэгцээний таамаг дахин тооцоолсон", "ml", None, summary.get("model_name", "model"))
            return self.send(200, {"ok": True, **summary})
        except Exception as exc:
            raise Error(500, f"Таамгийг шинэчилж чадсангүй. {str(exc)[:180]}")

    def ml_summary(self):
        with connect() as db:
            latest = db.execute("SELECT * FROM model_runs ORDER BY id DESC LIMIT 1").fetchone()
            rows = [dict(r) for r in db.execute("SELECT model_name,train_rows,test_rows,mae,rmse,bias,status,note,generated_at,data_version FROM model_runs ORDER BY id DESC LIMIT 10")]
        return self.send(200, {"latest": dict(latest) if latest else None, "runs": rows})

    def settings_get(self):
        with connect() as db:
            rows = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM app_settings")}
        return self.send(200, rows)

    def settings_update(self, s):
        data = self.json_body()
        allowed = {"default_loan_hours": (1, 168), "low_stock_threshold": (0, 99), "prediction_horizon_days": (1, 30)}
        updates = {}
        for k,(lo,hi) in allowed.items():
            if k in data:
                updates[k] = str(safe_int(data[k], lo, lo, hi))
        if "notifications_enabled" in data:
            updates["notifications_enabled"] = "1" if bool(data["notifications_enabled"]) else "0"
        with connect() as db:
            for k,v in updates.items():
                db.execute("INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k,v))
            log_event(db, s["id"], "Системийн тохиргоо шинэчилсэн", "settings", None, json.dumps(updates, ensure_ascii=False))
        return self.send(200, {"ok": True, **updates})

    def update_user_role(self, s, uid):
        data = self.json_body()
        role = data.get("role")
        if role not in ("admin", "staff", "student"):
            raise Error(400, "Хэрэглэгчийн төрөл буруу байна.")
        if uid == s["id"] and role != "admin":
            raise Error(400, "Өөрийн админ эрхийг энэ дэлгэцээс бууруулахгүй.")
        with connect() as db:
            user = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
            if not user:
                raise Error(404, "Хэрэглэгч олдсонгүй.")
            legacy_role = "admin" if role == "admin" else "viewer"
            db.execute("UPDATE users SET role=?,user_type=? WHERE id=?", (legacy_role, role, uid))
            log_event(db, s["id"], "Хэрэглэгчийн эрх өөрчилсөн", "user", uid, role)
        return self.send(200, {"ok": True})

    def predictions(self):
        with connect() as db:
            rows = db.execute("""SELECT p.*,s.course,s.lab_no,s.start_at,s.end_at,
                                      COALESCE((SELECT COUNT(*) FROM equipment e WHERE e.name=p.equipment_name AND e.status='available'),0) available_now
                               FROM predictions p JOIN lab_sessions s ON s.id=p.session_id
                               WHERE s.start_at >= ? ORDER BY s.start_at,p.equipment_name""", (iso_utc(),)).fetchall()
        out = []
        for r in rows:
            x = dict(r)
            # A data-coverage score is a reliability signal, not a probability.
            x["data_confidence"] = min(98, 55 + x["data_points"] * 4)
            x["status"] = "low" if x["processed_qty"] <= x["available_now"] else "warning"
            x["status_label"] = "Хангалттай" if x["status"] == "low" else "Анхаарах"
            out.append(x)
        return self.send(200, out)

    def recommendations(self):
        with connect() as db:
            rows = [dict(r) for r in db.execute("""SELECT r.*,p.equipment_name,p.raw_prediction,p.processed_qty,p.range_low,p.range_high,
                                                       s.course,s.lab_no,s.start_at
                                                FROM recommendations r JOIN predictions p ON p.id=r.prediction_id
                                                JOIN lab_sessions s ON s.id=p.session_id
                                                WHERE s.start_at >= ? ORDER BY CASE r.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,s.start_at""", (iso_utc(),))]
        return self.send(200, rows)

    def update_recommendation_status(self, s, rid):
        data = self.json_body()
        status = data.get("status")
        if status not in ("open", "accepted", "dismissed"):
            raise Error(400, "Зөвлөмжийн төлөв буруу байна.")
        with connect() as db:
            cur = db.execute("UPDATE recommendations SET decision_status=? WHERE id=?", (status, rid))
            if cur.rowcount == 0:
                raise Error(404, "Зөвлөмж олдсонгүй.")
            log_event(db, s["id"], "Зөвлөмжийн төлөв өөрчилсөн", "recommendation", rid, status)
        return self.send(200, {"ok": True})

    def dashboard(self, s):
        with connect() as db:
            total = db.execute("SELECT COUNT(*) FROM equipment").fetchone()[0]
            counts = {status: db.execute("SELECT COUNT(*) FROM equipment WHERE status=?", (status,)).fetchone()[0] for status in STATUSES}
            overdue = db.execute("SELECT COUNT(*) FROM loans WHERE returned_at IS NULL AND due_at < ?", (iso_utc(),)).fetchone()[0]
            active_users = db.execute("SELECT COUNT(DISTINCT borrower) FROM loans WHERE returned_at IS NULL").fetchone()[0]
            recent = [dict(r) for r in db.execute("""SELECT e.action,e.entity_type,e.entity_id,e.detail,e.created_at,COALESCE(NULLIF(TRIM(u.first_name||' '||u.last_name),''),u.username,'system') actor
                                                        FROM audit_events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 8""")]
            utilization = []
            for days in range(6, -1, -1):
                day = (utc_now() - timedelta(days=days)).date().isoformat()
                n = db.execute("SELECT COUNT(*) FROM loans WHERE date(issued_at)=?", (day,)).fetchone()[0]
                utilization.append({"label": day[5:], "value": n})
            status_chart = [{"label": "Боломжтой", "value": counts["available"]}, {"label": "Ашиглаж байна", "value": counts["in_use"]}, {"label": "Засварт", "value": counts["maintenance"]}, {"label": "Идэвхгүй", "value": counts["retired"]}]
            settings = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM app_settings")}
            top_demand = [dict(r) for r in db.execute("""SELECT p.equipment_name,
                                                    MAX(p.range_low) range_low,MAX(p.range_high) range_high,
                                                    MAX(p.processed_qty) processed_qty,
                                                    MAX(COALESCE((SELECT COUNT(*) FROM equipment e WHERE e.name=p.equipment_name AND e.status='available'),0)) available
                                             FROM predictions p JOIN lab_sessions s ON s.id=p.session_id
                                             WHERE s.start_at >= ? GROUP BY p.equipment_name ORDER BY processed_qty DESC LIMIT 4""", (iso_utc(),))]
        with connect() as db:
            self.ensure_notifications(db, s["id"])
            alerts = [dict(r) for r in db.execute("SELECT id,title,message,severity,created_at,read_at,entity_type,entity_id FROM notifications WHERE user_id=? ORDER BY read_at IS NOT NULL,id DESC LIMIT 5", (s["id"],))]
        return self.send(200, {
            "total": total,
            "available": counts["available"],
            "in_use": counts["in_use"],
            "maintenance": counts["maintenance"],
            "retired": counts["retired"],
            "damaged": counts["damaged"],
            "lost": counts["lost"],
            "overdue": overdue,
            "active_users": active_users,
            "utilization": utilization,
            "status_chart": status_chart,
            "demand": top_demand,
            "recent": recent,
            "alerts": alerts,
            "default_loan_hours": safe_int(settings.get("default_loan_hours"), 24, 1, 168),
            "prediction_horizon_days": safe_int(settings.get("prediction_horizon_days"), 7, 1, 30),
        })

    def reports(self):
        with connect() as db:
            top = [dict(r) for r in db.execute("""SELECT e.name,COUNT(l.id) uses FROM equipment e LEFT JOIN loans l ON l.equipment_id=e.id GROUP BY e.id ORDER BY uses DESC,e.name LIMIT 8""")]
            low = [dict(r) for r in db.execute("""SELECT e.name,COUNT(l.id) uses FROM equipment e LEFT JOIN loans l ON l.equipment_id=e.id GROUP BY e.id ORDER BY uses ASC,e.name LIMIT 8""")]
            maintenance = [dict(r) for r in db.execute("""SELECT e.name,m.issue,m.priority,m.scheduled_at,m.completed_at,m.outcome FROM maintenance m JOIN equipment e ON e.id=m.equipment_id ORDER BY m.id DESC LIMIT 10""")]
            loans = [dict(r) for r in db.execute("""SELECT e.name,COUNT(*) total_loans,SUM(CASE WHEN l.returned_at IS NULL THEN 1 ELSE 0 END) open_loans,
                                                               SUM(CASE WHEN l.returned_at IS NULL AND l.due_at < ? THEN 1 ELSE 0 END) overdue
                                                        FROM loans l JOIN equipment e ON e.id=l.equipment_id GROUP BY e.id ORDER BY total_loans DESC""", (iso_utc(),))]
            user_usage = [dict(r) for r in db.execute("""SELECT l.borrower,COUNT(*) uses FROM loans l GROUP BY l.borrower ORDER BY uses DESC LIMIT 10""")]
            total_assets = db.execute("SELECT COUNT(*) FROM equipment WHERE status<>'retired'").fetchone()[0]
            used_assets = db.execute("SELECT COUNT(*) FROM equipment WHERE status='in_use'").fetchone()[0]
            util = round((used_assets / total_assets * 100), 1) if total_assets else 0
        return self.send(200, {"utilization": util, "top_used": top, "low_used": low, "maintenance": maintenance, "loans": loans, "user_usage": user_usage})

    def report_export(self):
        import csv
        import io
        with connect() as db:
            rows = db.execute("""SELECT e.code,e.name,e.category,e.location,e.status,
                                      COUNT(l.id) total_loans,
                                      SUM(CASE WHEN l.returned_at IS NULL THEN 1 ELSE 0 END) open_loans,
                                      SUM(CASE WHEN l.returned_at IS NULL AND l.due_at < ? THEN 1 ELSE 0 END) overdue
                               FROM equipment e LEFT JOIN loans l ON l.equipment_id=e.id
                               GROUP BY e.id ORDER BY e.name""", (iso_utc(),)).fetchall()
        buff = io.StringIO()
        writer = csv.writer(buff)
        writer.writerow(["Төхөөрөмж", "Нэр", "Ангилал", "Байршил", "Төлөв", "Нийт олголт", "Нээлттэй", "Хугацаа хэтэрсэн"])
        for r in rows:
            writer.writerow([r["code"], r["name"], r["category"], r["location"], r["status"], r["total_loans"] or 0, r["open_loans"] or 0, r["overdue"] or 0])
        return self.send(200, buff.getvalue().encode("utf-8-sig"), content_type="text/csv; charset=utf-8", download=True)

    def audit_all(self):
        with connect() as db:
            rows = [dict(r) for r in db.execute("""SELECT e.action,e.entity_type,e.entity_id,e.detail,e.created_at,
                                                       COALESCE(NULLIF(TRIM(u.first_name||' '||u.last_name),''),u.username,'system') actor
                                                FROM audit_events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 150""")]
        return self.send(200, rows)

    def list_maintenance(self):
        with connect() as db:
            rows = [dict(r) for r in db.execute("""SELECT m.*,e.code,e.name,COALESCE(NULLIF(TRIM(u.first_name||' '||u.last_name),''),u.username) actor
                                                    FROM maintenance m JOIN equipment e ON e.id=m.equipment_id JOIN users u ON u.id=m.created_by
                                                    ORDER BY CASE m.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,m.id DESC""")]
        return self.send(200, rows)

    def create_maintenance(self, s):
        data = self.json_body()
        eid = safe_int(data.get("equipment_id"), -1, 1)
        issue = clean(data.get("issue"), 300, True)
        priority = data.get("priority", "medium")
        scheduled = clean(data.get("scheduled_at", ""), 64)
        if priority not in ("critical", "high", "medium", "low"):
            raise Error(400, "Эрэмбэ буруу байна.")
        with connect() as db:
            if not db.execute("SELECT 1 FROM equipment WHERE id=?", (eid,)).fetchone():
                raise Error(404, "Төхөөрөмж олдсонгүй.")
            cur = db.execute("INSERT INTO maintenance(equipment_id,issue,priority,scheduled_at,created_by) VALUES(?,?,?,?,?)", (eid, issue, priority, scheduled or None, s["id"]))
            db.execute("UPDATE equipment SET status='maintenance',updated_at=CURRENT_TIMESTAMP WHERE id=?", (eid,))
            mid = cur.lastrowid
            log_event(db, s["id"], "Засварын ажил үүсгэсэн", "maintenance", mid, issue)
        return self.send(201, {"id": mid})

    def update_maintenance(self, s, mid):
        data = self.json_body()
        outcome = clean(data.get("outcome", ""), 400)
        completed = clean(data.get("completed_at", ""), 64)
        with connect() as db:
            item = db.execute("SELECT * FROM maintenance WHERE id=?", (mid,)).fetchone()
            if not item:
                raise Error(404, "Засварын бүртгэл олдсонгүй.")
            db.execute("UPDATE maintenance SET outcome=?,completed_at=? WHERE id=?", (outcome, completed or iso_utc(), mid))
            db.execute("UPDATE equipment SET status='available',updated_at=CURRENT_TIMESTAMP WHERE id=?", (item["equipment_id"],))
            log_event(db, s["id"], "Засвар дууссан", "maintenance", mid, outcome)
        return self.send(200, {"ok": True})

    def notifications(self, s):
        with connect() as db:
            self.ensure_notifications(db, s["id"])
            rows = [dict(r) for r in db.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY read_at IS NOT NULL, id DESC LIMIT 50", (s["id"],))]
        return self.send(200, rows)

    def ensure_notifications(self, db, uid):
        settings = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM app_settings")}
        if settings.get("notifications_enabled", "1") != "1":
            return
        row = db.execute("SELECT role,user_type FROM users WHERE id=?", (uid,)).fetchone()
        role = effective_role(row) if row else "student"
        overdue = db.execute("SELECT COUNT(*) FROM loans WHERE issued_by=? AND returned_at IS NULL AND due_at < ?", (uid, iso_utc())).fetchone()[0]
        if overdue:
            fp = f"overdue:{uid}:{datetime.now(timezone.utc).date()}"
            db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint) VALUES(?,?,?,?,?,?)", (uid, "deadline", "Буцаах хугацаа хэтэрсэн", f"Таны {overdue} төхөөрөмжийн буцаах хугацаа хэтэрсэн байна.", "warning", fp))
        due_soon = db.execute("SELECT COUNT(*) FROM loans WHERE issued_by=? AND returned_at IS NULL AND due_at >= ? AND due_at < ?", (uid, iso_utc(), iso_utc(utc_now()+timedelta(hours=24)))).fetchone()[0]
        if due_soon:
            fp = f"due-soon:{uid}:{datetime.now(timezone.utc).date()}"
            db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint) VALUES(?,?,?,?,?,?)", (uid, "deadline", "Буцаах хугацаа ойртлоо", f"{due_soon} төхөөрөмжийн буцаах хугацаа 24 цагийн дотор байна.", "info", fp))

        req_rows = db.execute("SELECT id,equipment_name,status,version,rejection_reason FROM requests WHERE requested_by=? AND status IN ('approved','rejected') ORDER BY id DESC LIMIT 8", (uid,)).fetchall()
        for r in req_rows:
            fp = f"request:{r['id']}:{r['status']}:{r['version']}"
            title = "Төхөөрөмжийн хүсэлт батлагдлаа" if r["status"] == "approved" else "Төхөөрөмжийн хүсэлт татгалзлаа"
            message = f"{r['equipment_name']} хүсэлт батлагдлаа. QR-аар авах боломжтой." if r["status"] == "approved" else f"{r['equipment_name']} хүсэлт татгалзлаа. {r['rejection_reason']}"
            db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint,entity_type,entity_id) VALUES(?,?,?,?,?,?,?,?)", (uid, "request", title, message, "info" if r["status"] == "approved" else "warning", fp, "request", r["id"]))

        if role in ("admin", "viewer", "staff"):
            threshold = safe_int(settings.get("low_stock_threshold"), 2, 0, 99)
            low_items = db.execute("SELECT name,COUNT(*) available_count FROM equipment WHERE status='available' GROUP BY name HAVING COUNT(*)<=? ORDER BY available_count ASC", (threshold,)).fetchall()
            for item in low_items:
                fp = f"low:{item['name']}:{datetime.now(timezone.utc).date()}"
                db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint,entity_type) VALUES(?,?,?,?,?,?,?)", (uid, "stock", "Төхөөрөмж цөөн үлдсэн", f"{item['name']} одоогоор {item['available_count']} ширхэг боломжтой байна.", "warning", fp, "equipment"))
            maintenance = db.execute("SELECT e.name,m.priority FROM maintenance m JOIN equipment e ON e.id=m.equipment_id WHERE m.completed_at IS NULL ORDER BY CASE m.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 ELSE 2 END LIMIT 5").fetchall()
            for item in maintenance:
                fp = f"maintenance:{item['name']}"
                db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint,entity_type) VALUES(?,?,?,?,?,?,?)", (uid, "maintenance", "Засвар үйлчилгээ шаардлагатай", f"{item['name']} дээр засвар үйлчилгээний ажил нээлттэй байна.", "warning" if item["priority"] != "critical" else "critical", fp, "maintenance"))
            recs = db.execute("SELECT r.id,p.equipment_name,r.shortage_qty FROM recommendations r JOIN predictions p ON p.id=r.prediction_id JOIN lab_sessions s ON s.id=p.session_id WHERE r.shortage_qty>0 AND r.decision_status='open' AND s.start_at>=? LIMIT 5", (iso_utc(),)).fetchall()
            for r in recs:
                fp = f"rec:{r['id']}"
                db.execute("INSERT OR IGNORE INTO notifications(user_id,kind,title,message,severity,fingerprint) VALUES(?,?,?,?,?,?)", (uid, "demand", "Хэрэгцээ нэмэгдэх төлөвтэй", f"{r['equipment_name']} дээр {r['shortage_qty']} ширхэгийн дутагдал байж болзошгүй.", "critical", fp))
        db.commit()

    def mark_notification(self, s, nid):
        with connect() as db:
            cur = db.execute("UPDATE notifications SET read_at=? WHERE id=? AND user_id=?", (iso_utc(), nid, s["id"]))
            if cur.rowcount == 0:
                raise Error(404, "Мэдэгдэл олдсонгүй.")
        return self.send(200, {"ok": True})

    def mark_all_notifications(self, s):
        with connect() as db:
            db.execute("UPDATE notifications SET read_at=COALESCE(read_at,?) WHERE user_id=?", (iso_utc(), s["id"]))
        return self.send(200, {"ok": True})

    def users_list(self):
        with connect() as db:
            rows = [public_user(r) for r in db.execute("SELECT * FROM users ORDER BY id DESC")]
        return self.send(200, rows)

    def handle_request(self):
        try:
            self.dispatch()
        except Error as e:
            self.send(e.code, {"error": e.message})
        except sqlite3.IntegrityError as e:
            msg = "Мэдээлэл давхардсан эсвэл холбоосын алдаа гарлаа."
            if "equipment.code" in str(e):
                msg = "Төхөөрөмжийн дугаар давхардсан байна."
            elif "users.email" in str(e):
                msg = "Энэ и-мэйл бүртгэлтэй байна."
            self.send(409, {"error": msg})
        except Exception as e:
            print(type(e).__name__, str(e), flush=True)
            self.send(500, {"error": "Уучлаарай, мэдээллийг ачаалж чадсангүй."})

    do_GET = do_POST = do_PUT = handle_request


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LabSmart local laboratory management server")
    parser.add_argument("--demo", action="store_true", help="Seed demo users, equipment, sessions, predictions and history if the database is empty")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    initialize(args.demo)
    print(f"Open http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
