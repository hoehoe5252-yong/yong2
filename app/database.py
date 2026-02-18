import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_DB = _ROOT / "data" / "news.db"
DB_PATH = Path(os.environ.get("NEWS_DB_PATH", str(_DEFAULT_DB)))


def _configure(conn: sqlite3.Connection) -> None:
    # Some environments restrict SQLite's default file locking/journaling.
    # Use an in-memory journal to avoid disk I/O errors.
    conn.execute("PRAGMA journal_mode=MEMORY")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        _configure(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                image_url TEXT,
                published_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                is_like INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                removed_at TEXT,
                FOREIGN KEY(article_id) REFERENCES articles(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keyword_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                keyword_norm TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                image_url TEXT,
                published_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keyword_bookmarks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword_article_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                removed_at TEXT,
                FOREIGN KEY(keyword_article_id) REFERENCES keyword_articles(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keyword_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                keyword TEXT NOT NULL,
                keyword_norm TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_columns(
            conn,
            "articles",
            {
                "source_id": "TEXT",
                "image_url": "TEXT",
                "keyword": "TEXT",
                "keyword_norm": "TEXT",
            },
        )
        _ensure_columns(
            conn,
            "bookmarks",
            {
                "is_auto": "INTEGER DEFAULT 0",
            },
        )


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for name, col_type in columns.items():
        if name in existing:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        _configure(conn)
        yield conn
    finally:
        conn.close()
