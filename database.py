import sqlite3
import json
import datetime
import numpy as np
import os
import threading

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, 'visitor_db.sqlite')
SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

# CRITICAL FIX: Create a lock to prevent database collisions
db_lock = threading.Lock()

def get_settings():
    if not os.path.exists(SETTINGS_PATH):
        with open(SETTINGS_PATH, 'w') as f: json.dump({"cooldown": 30, "tolerance": 0.5}, f)
    with open(SETTINGS_PATH, 'r') as f: return json.load(f)

def save_settings(c, t):
    with open(SETTINGS_PATH, 'w') as f: json.dump({"cooldown": int(c), "tolerance": float(t)}, f)

def get_db_connection():
    # CRITICAL FIX: allow multiple threads
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db_connection()
        conn.execute('CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, is_staff INTEGER DEFAULT 0, visit_count INTEGER DEFAULT 0, is_banned INTEGER DEFAULT 0, embedding TEXT, created_at TEXT, last_seen TEXT)')
        conn.execute('CREATE TABLE IF NOT EXISTS visits (id INTEGER PRIMARY KEY AUTOINCREMENT, person_id INTEGER, timestamp TEXT, snapshot_path TEXT)')
        conn.commit()
        conn.close()

def load_all_encodings():
    with db_lock:
        conn = get_db_connection()
        rows = conn.execute('SELECT * FROM people').fetchall()
        conn.close()
    
    ids, encs, meta = [], [], {}
    for r in rows:
        try:
            ids.append(r['id'])
            encs.append(np.array(json.loads(r['embedding'])))
            meta[r['id']] = {"name": r['name'], "is_staff": bool(r['is_staff']), "is_banned": bool(r['is_banned']), "visit_count": r['visit_count']}
        except: pass
    return ids, encs, meta

def save_new_person(name, enc, is_staff=False):
    encoding_json = json.dumps(enc.tolist())
    now = datetime.datetime.now().isoformat()
    
    with db_lock:
        conn = get_db_connection()
        cur = conn.execute('INSERT INTO people (name, is_staff, visit_count, is_banned, embedding, created_at, last_seen) VALUES (?, ?, ?, 0, ?, ?, ?)', 
                     (name, 1 if is_staff else 0, 0 if is_staff else 1, encoding_json, now, now))
        conn.commit()
        pid = cur.lastrowid
        conn.close()
    return pid

def update_visit(pid, snap):
    now = datetime.datetime.now().isoformat()
    with db_lock:
        conn = get_db_connection()
        conn.execute('UPDATE people SET visit_count = visit_count + 1, last_seen = ? WHERE id = ?', (now, pid))
        conn.execute('INSERT INTO visits (person_id, timestamp, snapshot_path) VALUES (?, ?, ?)', (pid, now, snap))
        conn.commit()
        conn.close()