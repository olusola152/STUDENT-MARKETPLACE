"""Thin PostgreSQL layer for Supabase. One connection per request."""

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import current_app, g


def get_db():
    if "db" not in g:
        url = current_app.config["DATABASE_URL"]
        if not url:
            raise RuntimeError(
                "DATABASE_URL is empty. Copy .env.example to .env and paste your "
                "Supabase connection string into it."
            )
        g.db = psycopg2.connect(url, cursor_factory=RealDictCursor)
    return g.db


def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql, params=None, one=False):
    """SELECT. Returns a list of dict-like rows, or one row / None when one=True."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, params=None, returning=False):
    """INSERT / UPDATE / DELETE. Commits.

    With returning=True the statement must end in RETURNING ...; the first
    column of the returned row is handed back (normally the new id).
    """
    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            value = None
            if returning:
                row = cur.fetchone()
                value = next(iter(row.values())) if row else None
        conn.commit()
        return value
    except Exception:
        conn.rollback()
        raise


def scalar(sql, params=None, default=0):
    """Convenience for COUNT(*) style queries."""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
    if not row:
        return default
    value = next(iter(row.values()))
    return default if value is None else value
