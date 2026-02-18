from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, AnyUrl

from .crawler import crawl_keyword_news, crawl_source
from .database import get_conn, init_db
from .source_registry import get_source_by_id, load_sources


app = FastAPI(title="News Curation MVP")
logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent
_MANUAL_IBOSS_PATH = Path(
    os.environ.get("MANUAL_IBOSS_PATH", str(_ROOT / "data" / "iboss_manual.json"))
)
_IBOSS_MANUAL_ONLY = os.environ.get("IBOSS_MANUAL_ONLY", "1") == "1"
_STARTUP_CRAWL_SOURCE_IDS = [
    s.strip()
    for s in os.environ.get("STARTUP_CRAWL_SOURCE_IDS", "yozm_it").split(",")
    if s.strip()
]
_DEFAULT_KEYWORDS = [
    "SSP",
    "버즈빌",
    "Chatgpt",
    "Claude",
    "생성형AI",
    "오퍼월",
    "Offerwall",
    "광고 상품",
    "Retention",
    "Unity",
    "Admob",
    "APPLovin",
    "MAX",
]
_KEYWORD_NEWS_DAYS = int(os.environ.get("KEYWORD_NEWS_DAYS", "30"))
_KEYWORD_NEWS_MAX_ITEMS = int(os.environ.get("KEYWORD_NEWS_MAX_ITEMS", "30"))
_KEYWORD_NEWS_SOURCES = [
    s.strip()
    for s in os.environ.get("KEYWORD_NEWS_SOURCES", "google,naver").split(",")
    if s.strip()
]
_PRUNE_UNBOOKMARKED_DAYS = int(os.environ.get("PRUNE_UNBOOKMARKED_DAYS", "0"))


class ArticleOut(BaseModel):
    id: int
    source_id: Optional[str]
    source_name: Optional[str]
    title: str
    url: AnyUrl
    summary: str
    image_url: Optional[AnyUrl]
    published_at: Optional[str]


class FeedbackIn(BaseModel):
    article_id: int
    is_like: bool


class FeedbackOut(BaseModel):
    id: int
    article_id: int
    is_like: bool
    created_at: str


class CrawlIn(BaseModel):
    source_id: str


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    _ensure_default_keywords()
    sync_result = sync_manual_iboss_articles()
    logger.info(
        "[manual_iboss_sync] loaded=%s inserted=%s path=%s",
        sync_result["loaded"],
        sync_result["inserted"],
        sync_result["path"],
    )
    for result in sync_startup_sources():
        if result.get("error"):
            logger.warning(
                "[startup_source_sync_failed] source_id=%s error=%s",
                result.get("source_id"),
                result.get("error"),
            )
            continue
        logger.info(
            "[startup_source_sync] source_id=%s inserted=%s",
            result.get("source_id"),
            result.get("inserted", 0),
        )


@app.get("/news", response_model=List[ArticleOut])
def list_news(limit: int = 50) -> List[ArticleOut]:
    source_names = _source_name_map()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, source_id, title, url, summary, image_url, published_at
            FROM (
              SELECT id, source_id, title, url, summary, image_url, published_at
              FROM articles
              WHERE source_id IS NULL OR source_id != ?
              UNION ALL
              SELECT id, 'keyword_news' AS source_id, title, url, summary, image_url, published_at
              FROM keyword_articles
            )
            ORDER BY published_at DESC, id DESC
            LIMIT ?
            """,
            ("keyword_news", limit),
        )
        rows = cur.fetchall()
    return [
        ArticleOut(
            id=row[0],
            source_id=row[1],
            source_name=_display_source_name(row[1], row[3], source_names),
            title=row[2],
            url=row[3],
            summary=row[4],
            image_url=row[5],
            published_at=row[6],
        )
        for row in rows
    ]


@app.get("/", response_class=HTMLResponse)
def home(limit: int = 50) -> str:
    source_names = _source_name_map()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, title, url, summary, image_url, published_at, source_id
            FROM (
              SELECT id, title, url, summary, image_url, published_at, source_id
              FROM articles
              WHERE source_id IS NULL OR source_id != ?
              UNION ALL
              SELECT id, title, url, summary, image_url, published_at, 'keyword_news' AS source_id
              FROM keyword_articles
            )
            ORDER BY published_at DESC, id DESC
            LIMIT ?
            """,
            ("keyword_news", limit),
        )
        rows = cur.fetchall()

    cards = []
    for article_id, title, url, summary, image_url, published_at, source_id in rows:
        source_label = _display_source_name(source_id, url, source_names)
        logo_url = _source_logo_url(source_id)
        tags = _infer_tags(title, summary, source_id)
        tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in tags)
        recommended = _is_recommended(title, summary, tags)
        rec_html = '<span class="badge">추천</span>' if recommended else ""
        bookmark_action = f"/bookmark/{article_id}"
        bookmark_label = "찜"
        if source_id == "keyword_news":
            bookmark_action = f"/keyword-bookmark/{article_id}"
            bookmark_label = "찜됨"
        remove_action = f"/bookmark/{article_id}/remove"
        if source_id == "keyword_news":
            remove_action = f"/keyword-bookmark/{article_id}/remove"
        if logo_url:
            avatar_html = (
                '<div class="logo-wrap">'
                f'<img class="logo" src="{logo_url}" alt="{source_label} logo" loading="lazy" '
                'onerror="this.parentElement.classList.add(\'is-broken\');" />'
                f'<div class="logo-fallback">{source_label[:1].upper() if source_label else "?"}</div>'
                "</div>"
            )
        else:
            initial = (source_label[:1] or "?").upper()
            avatar_html = f'<div class="avatar avatar--placeholder" aria-hidden="true">{initial}</div>'
        if image_url:
            media_html = (
                '<div class="media">'
                f'<img src="{image_url}" alt="" loading="lazy" '
                'onerror="this.parentElement.classList.add(\'is-broken\');" />'
                f'<div class="media__ph">{source_label}</div>'
                "</div>"
            )
        else:
            media_html = f'<div class="media media--placeholder">{source_label}</div>'
        cards.append(
            f"""
            <article class="card">
              <header class="card__header">
                {avatar_html}
                <div class="meta">
                  <div class="source">{source_label}</div>
                  <div class="date">{published_at or ""}</div>
                </div>
                <form class="bookmark" method="post" action="{bookmark_action}">
                  <button type="submit">{bookmark_label}</button>
                </form>
              </header>
              <div class="tags">{tags_html}{rec_html}</div>
              <h2 class="title">
                <a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>
              </h2>
              <p class="summary">{summary or ""}</p>
              {media_html}
            </article>
            """
        )

    body = "\n".join(cards) if cards else "<p class=\"empty\">No articles yet.</p>"
    return f"""
    <html lang="ko">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>뉴스</title>
        <style>
          { _inline_shared_styles() }
        </style>
      </head>
      <body>
        <div class="topbar">
          <ul class="menu">
            <li class="active">수집 기사 목록</li>
            <li><a href="/bookmarks">찜한 기사</a></li>
            <li>설정</li>
          </ul>
        </div>
        <div class="container">
          { _render_trend_bars() }
          <div class="grid">{body}</div>
        </div>
      </body>
    </html>
    """


@app.get("/bookmarks", response_class=HTMLResponse)
def bookmarks(limit: int = 100) -> str:
    source_names = _source_name_map()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, title, url, summary, image_url, published_at, source_id
            FROM (
              SELECT a.id AS id,
                     a.title AS title,
                     a.url AS url,
                     a.summary AS summary,
                     a.image_url AS image_url,
                     a.published_at AS published_at,
                     a.source_id AS source_id,
                     b.created_at AS created_at
              FROM bookmarks b
              JOIN articles a ON a.id = b.article_id
              WHERE b.removed_at IS NULL
              UNION ALL
              SELECT k.id AS id,
                     k.title AS title,
                     k.url AS url,
                     k.summary AS summary,
                     k.image_url AS image_url,
                     k.published_at AS published_at,
                     'keyword_news' AS source_id,
                     kb.created_at AS created_at
              FROM keyword_bookmarks kb
              JOIN keyword_articles k ON k.id = kb.keyword_article_id
              WHERE kb.removed_at IS NULL
            )
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()

    cards = []
    for article_id, title, url, summary, image_url, published_at, source_id in rows:
        source_label = _display_source_name(source_id, url, source_names)
        logo_url = _source_logo_url(source_id)
        tags = _infer_tags(title, summary, source_id)
        tags_html = "".join(f'<span class="tag">{tag}</span>' for tag in tags)
        recommended = _is_recommended(title, summary, tags)
        rec_html = '<span class="badge">추천</span>' if recommended else ""
        if logo_url:
            avatar_html = (
                '<div class="logo-wrap">'
                f'<img class="logo" src="{logo_url}" alt="{source_label} logo" loading="lazy" '
                'onerror="this.parentElement.classList.add(\'is-broken\');" />'
                f'<div class="logo-fallback">{source_label[:1].upper() if source_label else "?"}</div>'
                "</div>"
            )
        else:
            initial = (source_label[:1] or "?").upper()
            avatar_html = f'<div class="avatar avatar--placeholder" aria-hidden="true">{initial}</div>'
        if image_url:
            media_html = (
                '<div class="media">'
                f'<img src="{image_url}" alt="" loading="lazy" '
                'onerror="this.parentElement.classList.add(\'is-broken\');" />'
                f'<div class="media__ph">{source_label}</div>'
                "</div>"
            )
        else:
            media_html = f'<div class="media media--placeholder">{source_label}</div>'
        cards.append(
            f"""
            <article class="card">
              <header class="card__header">
                {avatar_html}
                <div class="meta">
                  <div class="source">{source_label}</div>
                  <div class="date">{published_at or ""}</div>
                </div>
                <form class="bookmark" method="post" action="{remove_action}">
                  <button type="submit">제거</button>
                </form>
              </header>
              <div class="tags">{tags_html}{rec_html}</div>
              <h2 class="title">
                <a href="{url}" target="_blank" rel="noopener noreferrer">{title}</a>
              </h2>
              <p class="summary">{summary or ""}</p>
              {media_html}
            </article>
            """
        )

    body = "\n".join(cards) if cards else "<p class=\"empty\">No bookmarks yet.</p>"
    return f"""
    <html lang="ko">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>북마크</title>
        <style>
          { _inline_shared_styles() }
        </style>
      </head>
      <body>
        <div class="topbar">
          <ul class="menu">
            <li><a href="/">수집 기사 목록</a></li>
            <li class="active">찜한 기사</li>
            <li>설정</li>
          </ul>
        </div>
        <div class="container">
          { _render_trend_bars() }
          <div class="grid">{body}</div>
        </div>
      </body>
    </html>
    """


@app.get("/settings", response_class=HTMLResponse)
def settings() -> str:
    keywords = _list_keywords()
    active_rows = []
    inactive_rows = []
    for row in keywords:
        if row["is_active"]:
            active_rows.append(row)
        else:
            inactive_rows.append(row)

    def render_list(rows: List[dict]) -> str:
        if not rows:
            return '<p class="empty">등록된 키워드가 없습니다.</p>'
        items = []
        for row in rows:
            items.append(
                f"""
                <li class="keyword-item">
                  <span class="keyword-text">{row["keyword"]}</span>
                  <form method="post" action="/settings/keyword/{row["id"]}/remove">
                    <button type="submit">제거</button>
                  </form>
                </li>
                """
            )
        return f'<ul class="keyword-list">{"".join(items)}</ul>'

    active_html = render_list(active_rows)
    inactive_html = render_list(inactive_rows)

    return f"""
    <html lang="ko">
      <head>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>설정</title>
        <style>
          { _inline_shared_styles() }
        </style>
      </head>
      <body>
        <div class="topbar">
          <ul class="menu">
            <li><a href="/">수집 기사 목록</a></li>
            <li><a href="/bookmarks">찜한 기사</a></li>
            <li class="active">설정</li>
          </ul>
        </div>
        <div class="container">
          <div class="panel">
            <h2>키워드 설정</h2>
            <form class="keyword-form" method="post" action="/settings/keyword">
              <input type="text" name="keyword" placeholder="키워드를 입력하세요" />
              <button type="submit">추가</button>
            </form>
            <form class="keyword-form" method="post" action="/crawl-keywords">
              <button type="submit">지금 키워드 뉴스 수집</button>
            </form>
            <h3>활성 키워드</h3>
            {active_html}
            <h3 class="mt">비활성 키워드</h3>
            {inactive_html}
          </div>
        </div>
      </body>
    </html>
    """


@app.post("/settings/keyword")
def add_keyword(keyword: str = Form(...)) -> RedirectResponse:
    normalized = _normalize_keyword(keyword)
    if not normalized:
        return RedirectResponse(url="/settings", status_code=303)
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO keyword_settings (keyword, keyword_norm, is_active, created_at, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(keyword_norm) DO UPDATE SET
              keyword = excluded.keyword,
              is_active = 1,
              updated_at = excluded.updated_at
            """,
            (keyword.strip(), normalized, now, now),
        )
        conn.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/settings/keyword/{keyword_id}/remove")
def remove_keyword(keyword_id: int) -> RedirectResponse:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT keyword_norm FROM keyword_settings WHERE id = ?",
            (keyword_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="keyword not found")
        keyword_norm = row[0]
        conn.execute(
            """
            UPDATE keyword_settings
            SET is_active = 0, updated_at = ?
            WHERE id = ?
            """,
            (now, keyword_id),
        )
        _delete_keyword_articles(conn, keyword_norm)
        conn.commit()
    return RedirectResponse(url="/settings", status_code=303)


@app.post("/feedback", response_model=FeedbackOut)
def create_feedback(payload: FeedbackIn) -> FeedbackOut:
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT 1 FROM articles WHERE id = ?",
            (payload.article_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Article not found")
        created_at = datetime.utcnow().isoformat()
        cur = conn.execute(
            """
            INSERT INTO feedback (article_id, is_like, created_at)
            VALUES (?, ?, ?)
            """,
            (payload.article_id, 1 if payload.is_like else 0, created_at),
        )
        conn.commit()
        feedback_id = cur.lastrowid

    return FeedbackOut(
        id=feedback_id,
        article_id=payload.article_id,
        is_like=payload.is_like,
        created_at=created_at,
    )


@app.post("/crawl")
def crawl(payload: CrawlIn) -> dict:
    try:
        source = get_source_by_id(payload.source_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="source_id not found")
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        count = crawl_source(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"inserted": count, "source_id": payload.source_id}


@app.post("/crawl-keywords")
def crawl_keywords() -> dict:
    keywords = _active_keywords()
    result = crawl_keyword_news(
        keywords,
        days=_KEYWORD_NEWS_DAYS,
        max_items_per_keyword=_KEYWORD_NEWS_MAX_ITEMS,
        sources=_KEYWORD_NEWS_SOURCES,
    )
    return result


@app.post("/crawl-all")
def crawl_all() -> dict:
    sources = load_sources()
    results = []
    failed = 0
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            continue
        if source_id == "i_boss" and _IBOSS_MANUAL_ONLY:
            results.append({"source_id": source_id, "skipped": "manual_only"})
            continue
        try:
            count = crawl_source(source)
            results.append({"source_id": source_id, "inserted": count})
        except Exception as exc:
            failed += 1
            results.append(
                {
                    "source_id": source_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
    keyword_result = crawl_keyword_news(
        _active_keywords(),
        days=_KEYWORD_NEWS_DAYS,
        max_items_per_keyword=_KEYWORD_NEWS_MAX_ITEMS,
        sources=_KEYWORD_NEWS_SOURCES,
    )
    results.append({"source_id": "keyword_news", **keyword_result})
    pruned = _prune_unbookmarked_articles(_PRUNE_UNBOOKMARKED_DAYS)
    return {
        "results": results,
        "failed": failed,
        "ok": len(results) - failed,
        "pruned": pruned,
    }


@app.post("/sync-manual-iboss")
def sync_manual_iboss() -> dict:
    return sync_manual_iboss_articles()


def sync_manual_iboss_articles() -> dict:
    path = _MANUAL_IBOSS_PATH
    if not path.exists():
        return {"path": str(path), "loaded": 0, "inserted": 0}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[manual_iboss_sync_failed] path=%s error=%s", path, repr(exc))
        return {"path": str(path), "loaded": 0, "inserted": 0, "error": str(exc)}

    if isinstance(data, dict):
        raw_articles = data.get("articles") or []
    elif isinstance(data, list):
        raw_articles = data
    else:
        raw_articles = []

    inserted = 0
    loaded = 0
    with get_conn() as conn:
        for item in raw_articles:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            url = str(item.get("url") or "").strip()
            summary = str(item.get("summary") or "").strip()
            image_url = str(item.get("image_url") or "").strip() or None
            published_at = str(item.get("published_at") or "").strip() or None

            if not title or not url or not summary:
                continue
            loaded += 1
            cur = conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,))
            if cur.fetchone():
                continue
            conn.execute(
                """
                INSERT INTO articles (source_id, title, url, summary, image_url, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("i_boss", title, url, summary, image_url, published_at),
            )
            inserted += 1
        conn.commit()
    return {"path": str(path), "loaded": loaded, "inserted": inserted}


def sync_startup_sources() -> List[dict]:
    if not _STARTUP_CRAWL_SOURCE_IDS:
        return []
    results: List[dict] = []
    for source_id in _STARTUP_CRAWL_SOURCE_IDS:
        if source_id == "i_boss" and _IBOSS_MANUAL_ONLY:
            results.append({"source_id": source_id, "skipped": "manual_only"})
            continue
        try:
            source = get_source_by_id(source_id)
            inserted = crawl_source(source)
            results.append({"source_id": source_id, "inserted": inserted})
        except Exception as exc:
            results.append(
                {
                    "source_id": source_id,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            )
    return results


def _normalize_keyword(keyword: str) -> str:
    cleaned = re.sub(r"\s+", " ", keyword.strip())
    return cleaned.lower()


def _ensure_default_keywords() -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        for keyword in _DEFAULT_KEYWORDS:
            normalized = _normalize_keyword(keyword)
            if not normalized:
                continue
            conn.execute(
                """
                INSERT INTO keyword_settings (keyword, keyword_norm, is_active, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(keyword_norm) DO UPDATE SET
                  keyword = excluded.keyword,
                  is_active = 1,
                  updated_at = excluded.updated_at
                """,
                (keyword, normalized, now, now),
            )
        conn.commit()


def _list_keywords() -> List[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, keyword, keyword_norm, is_active, updated_at
            FROM keyword_settings
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "keyword": row[1],
            "keyword_norm": row[2],
            "is_active": bool(row[3]),
            "updated_at": row[4],
        }
        for row in rows
    ]


def _active_keywords() -> List[dict]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT keyword, keyword_norm
            FROM keyword_settings
            WHERE is_active = 1
            ORDER BY id ASC
            """
        )
        rows = cur.fetchall()
    return [{"keyword": row[0], "keyword_norm": row[1]} for row in rows]


def _delete_keyword_articles(conn, keyword_norm: str) -> None:
    cur = conn.execute(
        """
        SELECT id
        FROM keyword_articles
        WHERE keyword_norm = ?
        """,
        (keyword_norm,),
    )
    ids = [row[0] for row in cur.fetchall()]
    if ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"DELETE FROM keyword_bookmarks WHERE keyword_article_id IN ({placeholders})",
            ids,
        )
    conn.execute(
        """
        DELETE FROM keyword_articles
        WHERE keyword_norm = ?
        """,
        (keyword_norm,),
    )


def _prune_unbookmarked_articles(days: int) -> int:
    if days <= 0:
        return 0
    cutoff = (datetime.utcnow().date() - timedelta(days=days - 1)).isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """
            DELETE FROM articles
            WHERE id NOT IN (
              SELECT article_id FROM bookmarks WHERE removed_at IS NULL
            )
            AND (published_at IS NULL OR published_at < ?)
            """,
            (cutoff,),
        )
        conn.commit()
        return cur.rowcount or 0


def _source_name_map() -> Dict[str, str]:
    sources = load_sources()
    return {str(s.get("id")): str(s.get("name")) for s in sources if s.get("id") and s.get("name")}


def _display_source_name(source_id: Optional[str], url: str, source_names: Dict[str, str]) -> str:
    if source_id and source_id in source_names:
        return source_names[source_id]
    domain = urlparse(url).netloc
    return domain or (source_id or "")


def _source_logo_url(source_id: Optional[str]) -> Optional[str]:
    if source_id == "yozm_it":
        return "https://upload.wikimedia.org/wikipedia/commons/9/92/%EC%9A%94%EC%A6%98IT_%EB%A1%9C%EA%B3%A0.png"
    if source_id == "i_boss":
        return "https://cdn.ibos.kr/images/iboss_home_logo.svg"
    if source_id == "keyword_news":
        return "https://www.gstatic.com/images/branding/product/1x/googleg_32dp.png"
    return None


def _is_recommended(title: str, summary: str, tags: List[str]) -> bool:
    text = f"{title} {summary}".lower()
    pm_keywords = [
        "기획",
        "프로덕트",
        "product",
        "pm",
        "po",
        "로드맵",
        "roadmap",
        "전략",
        "launch",
        "strategy",
        "okr",
        "고객",
        "customer",
        "지표",
        "metric",
        "실험",
        "experiment",
    ]
    if any(tag in ["기획", "프로덕트", "비즈니스", "트렌드"] for tag in tags):
        return True
    return any(k in text for k in pm_keywords)


def _infer_tags(title: str, summary: str, source_id: Optional[str]) -> List[str]:
    if source_id == "keyword_news":
        return ["키워드"]
    text = f"{title} {summary}".lower()
    tag_keywords = {
        "기획": ["기획", "plan", "planning", "pm", "po", "roadmap", "strategy", "okr"],
        "디자인": ["디자인", "ux", "ui", "design", "prototype"],
        "개발": ["개발", "dev", "engineering", "code", "backend", "frontend", "api"],
        "AI": ["ai", "llm", "model", "inference", "agent"],
        "마케팅": ["마케팅", "marketing", "ad", "campaign", "brand", "performance"],
        "비즈니스": ["비즈니스", "business", "b2b", "b2c", "revenue", "growth"],
        "프로덕트": ["프로덕트", "product", "feature", "launch", "retention", "activation"],
        "커리어": ["커리어", "career", "hiring", "interview", "leadership"],
        "트렌드": ["트렌드", "trend", "market", "outlook", "report"],
        "스타트업": ["스타트업", "startup", "founder", "seed", "venture"],
    }

    tags: List[str] = []
    if source_id == "i_boss":
        tags.append("마케팅")

    for tag, keywords in tag_keywords.items():
        if tag in tags:
            continue
        if any(k in text for k in keywords):
            tags.append(tag)
        if len(tags) >= 3:
            break

    if not tags:
        tags.append("기획")
    return tags


def _po_pm_trend_items() -> List[Dict[str, str]]:
    return [
        {
            "title": "구글, 2026년 1월 AI 제품 업데이트 요약 공개",
            "source": "Google",
            "date": "2026-02-04",
            "url": "https://blog.google/innovation-and-ai/products/google-ai-updates-january-2026/",
        },
        {
            "title": "Gemini 3 Deep Think 업데이트 발표",
            "source": "Google",
            "date": "2026-02-12",
            "url": "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-deep-think/",
        },
        {
            "title": "Microsoft 365 Copilot 2026년 1월 업데이트",
            "source": "Microsoft",
            "date": "2026-01-30",
            "url": "https://techcommunity.microsoft.com/blog/microsoft365copilotblog/what%E2%80%99s-new-in-microsoft-365-copilot--january-2026/4488916",
        },
        {
            "title": "Meta, 2026년 AI 방향성 공개",
            "source": "Meta",
            "date": "2026-01-28",
            "url": "https://about.fb.com/news/2026/01/2026-ai-drives-performance/",
        },
        {
            "title": "Google I/O 2026 일정 공개",
            "source": "Google",
            "date": "2026-02-18",
            "url": "https://www.theverge.com/tech/880401/google-io-2026-dates-ai",
        },
    ]
def _martech_trend_items() -> List[Dict[str, str]]:
    return [
        {
            "title": "IAB 2026 전망: 미국 광고비 9.5% 성장, AI 우선순위 가속",
            "source": "IAB",
            "date": "2026-01-28",
            "url": "https://www.iab.com/news/outlook-study-forecasts-9-5-growth-in-u-s-ad-spend",
        },
        {
            "title": "IAB, 광고 측정 현대화를 위한 Project Eidos 발표",
            "source": "IAB",
            "date": "2026-02-02",
            "url": "https://www.iab.com/news/iab-announces-project-eidos",
        },
        {
            "title": "Google 2026 Ads & Commerce 전망: 유연·보조·개인화 경험",
            "source": "Google",
            "date": "2026-02-11",
            "url": "https://blog.google/products/ads-commerce/digital-advertising-commerce-2026/",
        },
        {
            "title": "Google Demand Gen 1월 업데이트: 쇼퍼블 CTV·여행 피드",
            "source": "Google",
            "date": "2026-01-28",
            "url": "https://blog.google/products/ads-commerce/demand-gen-drop-january-2026/",
        },
        {
            "title": "IAB Tech Lab ECAPI, 전환 이벤트 표준화 공개 의견 수렴",
            "source": "IAB Tech Lab",
            "date": "2026-01-20",
            "url": "https://iabtechlab.com/press-releases/iab-tech-lab-announces-event-and-conversion-api-ecapi-for-public-comment/",
        },
        {
            "title": "IAB Tech Lab, 상호운용 광고 실행을 위한 에이전틱 로드맵 공개",
            "source": "IAB Tech Lab",
            "date": "2026-01-06",
            "url": "https://iabtechlab.com/press-releases/iab-tech-lab-unveils-agentic-roadmap-for-digital-advertising/",
        },
        {
            "title": "Amazon Ads Brand+, AI 기반 잠재고객 발굴로 글로벌 출시",
            "source": "Amazon Ads",
            "date": "2026-01-30",
            "url": "https://advertising.amazon.com/en-gb/resources/whats-new/drive-brand-awareness-and-engagement-with-brand-plus",
        },
        {
            "title": "Amazon DSP, Podcast Audience Network 연동 추가",
            "source": "Amazon Ads",
            "date": "2026-01-01",
            "url": "https://advertising.amazon.com/resources/whats-new/podcast-audience-network-integration-with-amazon-dsp",
        },
        {
            "title": "IAB Tech Lab CTV Ad Portfolio 공개 의견 수렴 시작",
            "source": "IAB Tech Lab",
            "date": "2025-12-11",
            "url": "https://iabtechlab.com/press-releases/iab-tech-lab-announces-ctv-ad-portfolio/",
        },
        {
            "title": "IAB Tech Lab Deals API 1.0 공개, 프로그래매틱 딜 동기화 표준화",
            "source": "IAB Tech Lab",
            "date": "2025-12-04",
            "url": "https://iabtechlab.com/press-releases/iab-tech-lab-releases-deals-api-to-standardize-programmatic-deal-sync/",
        },
    ]
def _render_trend_bar(
    label: str,
    items: List[Dict[str, str]],
    limit: int = 5,
    track_class: str = "",
) -> str:
    rows = []
    for item in items[:limit]:
        rows.append(
            f"""
            <li class="trend__item">
              <span class="trend__meta">{item["source"]} | {item["date"]}</span>
              <a href="{item["url"]}" target="_blank" rel="noopener noreferrer">{item["title"]}</a>
            </li>
            """
        )
    body = "\n".join(rows) if rows else '<li class="trend__item">No items</li>'
    return f"""
      <div class="trend">
        <div class="trend__label">{label}</div>
        <div class="trend__viewport">
          <ul class="trend__track {track_class}">
            {body}
            {body}
          </ul>
        </div>
      </div>
    """
def _render_trend_bars() -> str:
    martech_html = _render_trend_bar(
        "MarTech 추천 트렌드",
        _martech_trend_items(),
        limit=10,
        track_class="trend__track--martech",
    )
    po_pm_html = _render_trend_bar("PO/PM 추천 트렌드", _po_pm_trend_items(), limit=5)
    return f"{martech_html}\n{po_pm_html}"
@app.post("/bookmark/{article_id}")
def add_bookmark(article_id: int) -> RedirectResponse:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bookmarks (article_id, created_at)
            VALUES (?, ?)
            ON CONFLICT(article_id) DO UPDATE SET removed_at = NULL
            """,
            (article_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
    return RedirectResponse(url="/bookmarks", status_code=303)


@app.post("/keyword-bookmark/{keyword_article_id}")
def add_keyword_bookmark(keyword_article_id: int) -> RedirectResponse:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO keyword_bookmarks (keyword_article_id, created_at)
            VALUES (?, ?)
            ON CONFLICT(keyword_article_id) DO UPDATE SET removed_at = NULL
            """,
            (keyword_article_id, datetime.utcnow().isoformat()),
        )
        conn.commit()
    return RedirectResponse(url="/bookmarks", status_code=303)
@app.post("/bookmark/{article_id}/remove")
def remove_bookmark(article_id: int) -> RedirectResponse:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE bookmarks
            SET removed_at = ?
            WHERE article_id = ?
            """,
            (datetime.utcnow().isoformat(), article_id),
        )
        conn.commit()
    return RedirectResponse(url="/bookmarks", status_code=303)


@app.post("/keyword-bookmark/{keyword_article_id}/remove")
def remove_keyword_bookmark(keyword_article_id: int) -> RedirectResponse:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE keyword_bookmarks
            SET removed_at = ?
            WHERE keyword_article_id = ?
            """,
            (datetime.utcnow().isoformat(), keyword_article_id),
        )
        conn.commit()
    return RedirectResponse(url="/bookmarks", status_code=303)
def _inline_shared_styles() -> str:
    return """
          :root {
            --bg: #f9fafb;
            --card: #ffffff;
            --text: #191f28;
            --muted: #6b7684;
            --border: #e5e8eb;
            --accent: #3182f6;
            --accent-soft: #e8f3ff;
          }
          * { box-sizing: border-box; }
          body {
            margin: 0;
            font-family: "SUIT", "Pretendard", "Noto Sans KR", "Segoe UI", sans-serif;
            color: var(--text);
            background: var(--bg);
          }
          .topbar {
            background: #ffffff;
            border-bottom: 1px solid var(--border);
            padding: 12px 24px;
            position: sticky;
            top: 0;
            z-index: 10;
          }
          .menu {
            display: flex;
            gap: 16px;
            list-style: none;
            margin: 0;
            padding: 0;
            font-weight: 600;
          }
          .menu li {
            padding: 8px 12px;
            border-radius: 8px;
            color: var(--muted);
          }
          .menu li.active {
            color: var(--accent);
            background: var(--accent-soft);
          }
          .menu a {
            color: inherit;
            text-decoration: none;
          }
          .container {
            max-width: 1200px;
            margin: 24px auto 64px;
            padding: 0 24px;
          }
          .trend {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 10px 12px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
            display: grid;
            grid-template-columns: 140px 1fr;
            gap: 12px;
            align-items: center;
            margin-bottom: 16px;
          }
          .trend__label {
            font-weight: 700;
            color: var(--accent);
            font-size: 13px;
            white-space: nowrap;
          }
          .trend__viewport {
            overflow: hidden;
          }
          .trend__track {
            list-style: none;
            margin: 0;
            padding: 0;
            display: flex;
            gap: 24px;
            width: max-content;
            animation: trend-scroll 28s linear infinite;
          }
          .trend__track--martech {
            animation-duration: 56s;
          }
          .trend__item {
            display: inline-flex;
            gap: 8px;
            align-items: center;
            white-space: nowrap;
          }
          .trend__item a {
            color: var(--text);
            text-decoration: none;
            font-weight: 600;
            font-size: 13px;
          }
          .trend__item a:hover {
            text-decoration: underline;
          }
          .trend__meta {
            color: var(--muted);
            font-size: 12px;
          }
          @keyframes trend-scroll {
            from { transform: translateX(0); }
            to { transform: translateX(-50%); }
          }
          .grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 16px;
          }
          .card {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            display: flex;
            flex-direction: column;
            gap: 10px;
          }
          .card__header {
            display: flex;
            gap: 10px;
            align-items: center;
          }
          .avatar {
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--accent);
            color: #ffffff;
            display: grid;
            place-items: center;
            font-weight: 700;
          }
          .avatar--placeholder {
            background: linear-gradient(135deg, #3182f6, #63a4ff);
          }
          .logo-wrap {
            width: 40px;
            height: 40px;
            border-radius: 8px;
            background: #ffffff;
            border: 1px solid var(--border);
            display: grid;
            place-items: center;
            overflow: hidden;
            position: relative;
          }
          .logo {
            width: 40px;
            height: 40px;
            object-fit: contain;
            padding: 4px;
            z-index: 1;
          }
          .logo-fallback {
            position: absolute;
            inset: 0;
            display: grid;
            place-items: center;
            background: linear-gradient(135deg, #e8f3ff, #f9fafb);
            color: var(--accent);
            font-weight: 700;
          }
          .logo-wrap.is-broken .logo {
            display: none;
          }
          .bookmark {
            margin-left: auto;
          }
          .bookmark button {
            border: 1px solid var(--border);
            background: #ffffff;
            color: var(--accent);
            padding: 6px 10px;
            border-radius: 999px;
            font-weight: 600;
            cursor: pointer;
          }
          .bookmark button:hover {
            background: var(--accent-soft);
          }
          .meta .source {
            font-weight: 700;
          }
          .meta .date {
            font-size: 12px;
            color: var(--muted);
          }
          .tags {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
          }
          .tag {
            display: inline-flex;
            align-items: center;
            padding: 4px 8px;
            border-radius: 999px;
            background: var(--accent-soft);
            color: var(--accent);
            font-size: 12px;
            font-weight: 600;
          }
          .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 8px;
            border-radius: 999px;
            background: #e8f3ff;
            color: #1b64da;
            font-size: 12px;
            font-weight: 700;
          }
          .title {
            margin: 0;
            font-size: 16px;
            line-height: 1.4;
          }
          .title a {
            color: var(--text);
            text-decoration: none;
          }
          .title a:hover {
            text-decoration: underline;
          }
          .summary {
            margin: 0;
            color: var(--text);
            font-size: 14px;
            line-height: 1.5;
          }
          .media {
            position: relative;
          }
          .media img {
            width: 100%;
            height: 180px;
            object-fit: cover;
            border-radius: 8px;
            border: 1px solid var(--border);
            position: relative;
            z-index: 1;
            background: #ffffff;
          }
          .media__ph {
            position: absolute;
            inset: 0;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: linear-gradient(135deg, #e8f3ff, #f9fafb);
            color: var(--accent);
            font-weight: 700;
            display: grid;
            place-items: center;
          }
          .media.is-broken img {
            display: none;
          }
          .media.is-broken .media__ph {
            position: static;
            height: 180px;
          }
          .media--placeholder {
            width: 100%;
            height: 180px;
            border-radius: 8px;
            border: 1px solid var(--border);
            background: linear-gradient(135deg, #e8f3ff, #f9fafb);
            color: var(--accent);
            font-weight: 700;
            display: grid;
            place-items: center;
          }
          .empty {
            color: var(--muted);
          }
          .panel {
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.04);
          }
          .panel h2 {
            margin: 0 0 12px;
            font-size: 18px;
          }
          .panel h3 {
            margin: 20px 0 10px;
            font-size: 14px;
            color: var(--muted);
          }
          .panel h3.mt {
            margin-top: 24px;
          }
          .keyword-form {
            display: flex;
            gap: 8px;
            margin-bottom: 8px;
          }
          .keyword-form input {
            flex: 1;
            padding: 10px 12px;
            border-radius: 8px;
            border: 1px solid var(--border);
            font-size: 14px;
          }
          .keyword-form button {
            border: 1px solid var(--border);
            background: var(--accent);
            color: #ffffff;
            padding: 8px 14px;
            border-radius: 8px;
            font-weight: 700;
            cursor: pointer;
          }
          .keyword-list {
            list-style: none;
            padding: 0;
            margin: 0;
            display: flex;
            flex-direction: column;
            gap: 8px;
          }
          .keyword-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 8px 12px;
            background: #ffffff;
          }
          .keyword-text {
            font-weight: 600;
          }
          .keyword-item button {
            border: 1px solid var(--border);
            background: #ffffff;
            color: var(--accent);
            padding: 6px 10px;
            border-radius: 999px;
            font-weight: 600;
            cursor: pointer;
          }
          @media (max-width: 1024px) {
            .grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .trend {
              grid-template-columns: 1fr;
              gap: 6px;
            }
          }
          @media (max-width: 768px) {
            .grid {
              grid-template-columns: 1fr;
            }
          }
    """

