from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, AnyUrl

from .crawler import crawl_source
from .database import get_conn, init_db
from .source_registry import get_source_by_id, load_sources


app = FastAPI(title="News Curation MVP")


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


@app.get("/news", response_model=List[ArticleOut])
def list_news(limit: int = 50) -> List[ArticleOut]:
    source_names = _source_name_map()
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT id, source_id, title, url, summary, image_url, published_at
            FROM articles
            ORDER BY published_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
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
            FROM articles
            ORDER BY published_at DESC, id DESC
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
                <form class="bookmark" method="post" action="/bookmark/{article_id}">
                  <button type="submit">찜</button>
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
    <html>
      <head>
        <title>News</title>
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
          { _render_trend_bar() }
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
            SELECT a.id, a.title, a.url, a.summary, a.image_url, a.published_at, a.source_id
            FROM bookmarks b
            JOIN articles a ON a.id = b.article_id
            WHERE b.removed_at IS NULL
            ORDER BY b.created_at DESC
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
                <form class="bookmark" method="post" action="/bookmark/{article_id}/remove">
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
    <html>
      <head>
        <title>Bookmarks</title>
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
          { _render_trend_bar() }
          <div class="grid">{body}</div>
        </div>
      </body>
    </html>
    """


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


@app.post("/crawl-all")
def crawl_all() -> dict:
    sources = load_sources()
    results = []
    for source in sources:
        source_id = str(source.get("id") or "").strip()
        if not source_id:
            continue
        try:
            count = crawl_source(source)
            results.append({"source_id": source_id, "inserted": count})
        except ValueError as exc:
            results.append({"source_id": source_id, "error": str(exc)})
    return {"results": results}


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
        "전략",
        "지표",
        "okr",
        "실험",
        "가설",
        "우선순위",
        "고객",
        "문제정의",
    ]
    if any(tag in ["기획", "프로덕트", "비즈니스", "트렌드"] for tag in tags):
        return True
    return any(k in text for k in pm_keywords)


def _infer_tags(title: str, summary: str, source_id: Optional[str]) -> List[str]:
    text = f"{title} {summary}".lower()
    tag_keywords = {
        "기획": ["기획", "pm", "po", "전략", "서비스 기획", "프로덕트 기획"],
        "디자인": ["디자인", "ux", "ui", "브랜딩", "그래픽"],
        "개발": ["개발", "엔지니어", "코드", "프로그래밍", "백엔드", "프론트", "dev"],
        "AI": ["ai", "인공지능", "머신러닝", "llm", "모델"],
        "마케팅": ["마케팅", "광고", "캠페인", "브랜드", "퍼포먼스"],
        "비즈니스": ["비즈니스", "사업", "매출", "b2b", "b2c"],
        "프로덕트": ["프로덕트", "제품", "서비스", "기능"],
        "커리어": ["커리어", "이직", "채용", "면접", "조직", "리더십"],
        "트렌드": ["트렌드", "시장", "동향", "리포트"],
        "스타트업": ["스타트업", "창업", "투자"],
    }

    tags: List[str] = []
    if source_id == "i_boss":
        tags.append("마케팅")

    for tag, keywords in tag_keywords.items():
        if tag in tags:
            continue
        if any(k.lower() in text for k in keywords):
            tags.append(tag)
        if len(tags) >= 3:
            break

    if not tags:
        tags.append("기획")
    return tags


def _sidebar_items() -> List[Dict[str, str]]:
    return [
        {
            "title": "Google, 1월 AI 업데이트 요약 공개",
            "source": "Google",
            "date": "2026-02-04",
            "url": "https://blog.google/innovation-and-ai/products/google-ai-updates-january-2026/",
        },
        {
            "title": "Gemini 3 Deep Think 업데이트 공개",
            "source": "Google",
            "date": "2026-02-12",
            "url": "https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-3-deep-think/",
        },
        {
            "title": "Microsoft 365 Copilot 1월 업데이트",
            "source": "Microsoft",
            "date": "2026-01-30",
            "url": "https://techcommunity.microsoft.com/blog/microsoft365copilotblog/what%E2%80%99s-new-in-microsoft-365-copilot--january-2026/4488916",
        },
        {
            "title": "Meta, 2026년 AI 성과 방향성 공개",
            "source": "Meta",
            "date": "2026-01-28",
            "url": "https://about.fb.com/news/2026/01/2026-ai-drives-performance/",
        },
        {
            "title": "Google I/O 2026 날짜 공개",
            "source": "Google",
            "date": "2026-02-18",
            "url": "https://www.theverge.com/tech/880401/google-io-2026-dates-ai",
        },
    ]


def _render_trend_bar() -> str:
    items = _sidebar_items()
    rows = []
    for item in items[:5]:
        rows.append(
            f"""
            <li class="trend__item">
              <span class="trend__meta">{item["source"]} · {item["date"]}</span>
              <a href="{item["url"]}" target="_blank" rel="noopener noreferrer">{item["title"]}</a>
            </li>
            """
        )
    body = "\n".join(rows) if rows else "<li class=\"trend__item\">No items</li>"
    return f"""
      <div class="trend">
        <div class="trend__label">PO/PM 추천 트렌드</div>
        <div class="trend__viewport">
          <ul class="trend__track">
            {body}
            {body}
          </ul>
        </div>
      </div>
    """


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
          @media (max-width: 1024px) {
            .grid {
              grid-template-columns: repeat(2, minmax(0, 1fr));
            }
            .trend {
              grid-template-columns: 1fr;
              gap: 6px;
            }
          }
          @media (max-width: 640px) {
            .grid {
              grid-template-columns: 1fr;
            }
          }
    """
