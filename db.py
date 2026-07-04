"""Tiny SQLite layer for tokens + scheduled posts. Good enough for a
single-user local/small deployment. Swap for Postgres if you outgrow it."""

import sqlite3
import json
import os
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            platform TEXT PRIMARY KEY,
            data_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_posts (
            id TEXT PRIMARY KEY,
            clip_path TEXT NOT NULL,
            clip_url TEXT NOT NULL,
            title TEXT,
            caption TEXT,
            platforms TEXT NOT NULL,
            scheduled_time REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            result_json TEXT,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


# --- tokens ---------------------------------------------------------------

def save_token(platform, data: dict):
    conn = get_conn()
    conn.execute(
        "INSERT INTO tokens (platform, data_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(platform) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at",
        (platform, json.dumps(data), time.time()),
    )
    conn.commit()
    conn.close()


def get_token(platform):
    conn = get_conn()
    row = conn.execute("SELECT data_json FROM tokens WHERE platform=?", (platform,)).fetchone()
    conn.close()
    return json.loads(row["data_json"]) if row else None


# --- scheduled posts --------------------------------------------------------

def add_scheduled_post(post_id, clip_path, clip_url, title, caption, platforms, scheduled_time):
    conn = get_conn()
    conn.execute(
        "INSERT INTO scheduled_posts (id, clip_path, clip_url, title, caption, platforms, scheduled_time, status, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
        (post_id, clip_path, clip_url, title, caption, json.dumps(platforms), scheduled_time, time.time()),
    )
    conn.commit()
    conn.close()


def list_scheduled_posts():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM scheduled_posts ORDER BY scheduled_time ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_due_posts():
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM scheduled_posts WHERE status='pending' AND scheduled_time<=?",
        (time.time(),),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_post_status(post_id, status, result=None):
    conn = get_conn()
    conn.execute(
        "UPDATE scheduled_posts SET status=?, result_json=? WHERE id=?",
        (status, json.dumps(result) if result else None, post_id),
    )
    conn.commit()
    conn.close()
