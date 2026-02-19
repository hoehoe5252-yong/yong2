from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import os
import re
import time
from typing import Iterable, Optional, Sequence
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from .database import get_conn

logger = logging.getLogger(__name__)


def _parse_published(entry) -> Optional[str]:
    if getattr(entry, "published_parsed", None):
        dt = datetime(*entry.published_parsed[:6])
        return dt.isoformat()
    if getattr(entry, "updated_parsed", None):
        dt = datetime(*entry.updated_parsed[:6])
        return dt.isoformat()
    return None


def _fetch_summary(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    return ""


def _iter_entries(rss_url: str) -> Iterable[object]:
    feed = feedparser.parse(rss_url)
    return feed.entries or []


def crawl_rss(rss_url: str) -> int:
    inserted = 0
    with get_conn() as conn:
        for entry in _iter_entries(rss_url):
            title = (getattr(entry, "title", "") or "").strip()
            url = (getattr(entry, "link", "") or "").strip()
            if not title or not url:
                continue
            summary = (getattr(entry, "summary", "") or "").strip()
            if not summary:
                summary = _fetch_summary(url)
            published_at = _parse_published(entry)

            cur = conn.execute(
                "SELECT 1 FROM articles WHERE url = ?",
                (url,),
            )
            if cur.fetchone():
                continue
            conn.execute(
                """
                INSERT INTO articles (title, url, summary, published_at)
                VALUES (?, ?, ?, ?)
                """,
                (title, url, summary, published_at),
            )
            inserted += 1
        conn.commit()
    return inserted


def crawl_keyword_news(
    keywords: Sequence[dict],
    *,
    days: int = 30,
    max_items_per_keyword: int = 30,
    sources: Sequence[str] | None = None,
) -> dict:
    if not keywords:
        return {"inserted": 0, "bookmarked": 0, "keywords": 0}

    inserted = 0
    bookmarked = 0
    today = date.today()
    created_at = datetime.utcnow().isoformat()
    source_set = {s.strip().lower() for s in (sources or []) if s.strip()}
    if not source_set:
        source_set = {"google"}

    with get_conn() as conn:
        for keyword in keywords:
            raw_keyword = str(keyword.get("keyword") or "").strip()
            keyword_norm = str(keyword.get("keyword_norm") or "").strip()
            if not raw_keyword or not keyword_norm:
                continue
            seen_urls: set[str] = set()
            seen_titles: set[str] = set()
            collected = 0
            if "google" in source_set:
                rss_url = _google_news_rss_url(raw_keyword)
                for entry in _iter_entries(rss_url):
                    title = (getattr(entry, "title", "") or "").strip()
                    url = (getattr(entry, "link", "") or "").strip()
                    if not title or not url:
                        continue
                    url = _normalize_google_news_url(url)
                    if url in seen_urls:
                        continue
                    press = _extract_google_press(entry, title)
                    display_title = _format_title_with_press(title, press)
                    title_key = _normalize_title_for_dedupe(display_title)
                    if title_key in seen_titles:
                        continue
                    seen_urls.add(url)
                    seen_titles.add(title_key)

                    published_at = _parse_published(entry)
                    if not _is_within_days(published_at, today, days=days):
                        continue
                    summary = (getattr(entry, "summary", "") or "").strip()
                    if not summary:
                        summary = _fetch_summary(url)

                    keyword_article_id, is_new = _upsert_keyword_article(
                        conn,
                        raw_keyword,
                        keyword_norm,
                        display_title,
                        url,
                        summary,
                        published_at,
                    )
                    if is_new:
                        inserted += 1
                    collected += 1
                    if collected >= max_items_per_keyword:
                        break

            if "naver" in source_set and collected < max_items_per_keyword:
                for item in _iter_naver_news_items(raw_keyword):
                    title = (item.get("title") or "").strip()
                    url = (item.get("url") or "").strip()
                    press = (item.get("press") or "").strip()
                    if not title or not url:
                        continue
                    if url in seen_urls:
                        continue
                    display_title = _format_title_with_press(title, press)
                    title_key = _normalize_title_for_dedupe(display_title)
                    if title_key in seen_titles:
                        continue
                    seen_urls.add(url)
                    seen_titles.add(title_key)
                    published_at = item.get("published_at")
                    if not _is_within_days(published_at, today, days=days):
                        continue
                    summary = (item.get("summary") or "").strip()
                    if not summary:
                        summary = _fetch_summary(url)
                    keyword_article_id, is_new = _upsert_keyword_article(
                        conn,
                        raw_keyword,
                        keyword_norm,
                        display_title,
                        url,
                        summary,
                        published_at,
                    )
                    if is_new:
                        inserted += 1
                    collected += 1
                    if collected >= max_items_per_keyword:
                        break
        conn.commit()

    return {"inserted": inserted, "bookmarked": bookmarked, "keywords": len(keywords)}


def crawl_source(source: dict) -> int:
    source_id = (source.get("id") or "").strip()
    start_url = source.get("start_url")
    start_urls = _normalize_start_urls(start_url)
    if not source_id or not start_urls:
        raise ValueError("source must include id and start_url")

    if source_id == "yozm_it":
        return crawl_yozm_it(start_urls, source_id)
    if source_id == "i_boss":
        return crawl_i_boss(start_urls[0], source_id)
    raise ValueError(f"unsupported source_id: {source_id}")


def crawl_yozm_it(start_urls: Sequence[str], source_id: str) -> int:
    items: list[dict] = []
    seen = set()
    for start_url in start_urls:
        try:
            html = _fetch_html(start_url)
        except requests.RequestException:
            # Skip a list page if the remote host closes the connection.
            continue
        soup = BeautifulSoup(html, "html.parser")
        for item in _extract_yozm_list_items(soup, start_url):
            url = item.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            items.append(item)

    inserted = 0
    today = date.today()
    max_links = 200
    with get_conn() as conn:
        for item in items[:max_links]:
            url = item["url"]
            detail = {}
            title = (item.get("title") or "").strip()
            summary = (item.get("summary") or "").strip()
            image_url = (item.get("image_url") or "").strip() or None
            published_at = item.get("published_at")

            if not published_at or not title or not summary or not image_url:
                detail = _fetch_yozm_detail(url)
                title = detail.get("title") or title
                summary = detail.get("summary") or summary
                image_url = detail.get("image_url") or image_url
                published_at = detail.get("published_at") or published_at

            if not title or not url:
                continue
            if not _is_within_days(published_at, today, days=30):
                continue
            cur = conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,))
            if cur.fetchone():
                continue
            conn.execute(
                """
                INSERT INTO articles (source_id, title, url, summary, image_url, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_id, title, url, summary, image_url, published_at),
            )
            inserted += 1
            if inserted >= 50:
                break
        conn.commit()
    return inserted


def crawl_i_boss(start_url: str, source_id: str) -> int:
    html = _fetch_html_with_retry(start_url, source_id=source_id, stage="list", attempts=3)
    soup = BeautifulSoup(html, "html.parser")

    items = _extract_iboss_list_items(soup, start_url)

    inserted = 0
    today = date.today()
    max_links = 200
    with get_conn() as conn:
        for item in items[:max_links]:
            url = item["url"]
            detail = {}
            title = (item.get("title") or "").strip()
            summary = (item.get("summary") or "").strip()
            image_url = (item.get("image_url") or "").strip() or None
            published_at = item.get("published_at")

            if not published_at or not title or not summary or not image_url:
                detail = _fetch_generic_detail_with_retry(url, source_id=source_id)
                title = detail.get("title") or title
                summary = detail.get("summary") or summary
                image_url = detail.get("image_url") or image_url
                published_at = detail.get("published_at") or published_at

            if not title or not url:
                continue
            if not _is_within_days(published_at, today, days=30):
                continue
            cur = conn.execute("SELECT 1 FROM articles WHERE url = ?", (url,))
            if cur.fetchone():
                continue
            conn.execute(
                """
                INSERT INTO articles (source_id, title, url, summary, image_url, published_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_id, title, url, summary, image_url, published_at),
            )
            inserted += 1
            if inserted >= 50:
                break
        conn.commit()
    return inserted


def _fetch_html(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yong2/0.1)"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.text


def _fetch_html_with_retry(
    url: str,
    *,
    source_id: str,
    stage: str,
    attempts: int = 3,
    backoff_seconds: float = 1.0,
) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yong2/0.1)"}
    use_proxy = source_id == "i_boss" and bool(os.getenv("SCRAPINGBEE_API_KEY", "").strip())
    proxy_url = os.getenv("SCRAPINGBEE_API_URL", "https://app.scrapingbee.com/api/v1/")
    for attempt in range(1, attempts + 1):
        try:
            if use_proxy:
                resp = requests.get(
                    proxy_url,
                    params={
                        "api_key": os.getenv("SCRAPINGBEE_API_KEY", "").strip(),
                        "url": url,
                        "render_js": "false",
                        "premium_proxy": "true",
                        "country_code": "kr",
                    },
                    headers=headers,
                    timeout=20,
                )
            else:
                resp = requests.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            logger.warning(
                "[crawl_retry] source=%s stage=%s attempt=%s/%s url=%s proxy=%s error=%s",
                source_id,
                stage,
                attempt,
                attempts,
                url,
                use_proxy,
                repr(exc),
            )
            if attempt == attempts:
                logger.error(
                    "[crawl_fail] source=%s stage=%s url=%s attempts=%s",
                    source_id,
                    stage,
                    url,
                    attempts,
                )
                raise
            time.sleep(backoff_seconds * attempt)


def _fetch_yozm_detail(url: str) -> dict:
    try:
        html = _fetch_html(url)
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    title = _meta_content(soup, "og:title") or _meta_content(soup, "twitter:title")
    summary = _meta_content(soup, "description") or _meta_content(soup, "og:description")
    image_url = _meta_content(soup, "og:image") or _meta_content(soup, "twitter:image")

    published_at = _extract_date_from_json_ld(soup) or _extract_date_near_title(soup) or _extract_date_anywhere(soup)

    return {
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "image_url": (image_url or "").strip() or None,
        "published_at": published_at,
    }


def _fetch_generic_detail(url: str) -> dict:
    try:
        html = _fetch_html(url)
    except requests.RequestException:
        return {}
    soup = BeautifulSoup(html, "html.parser")
    title = _meta_content(soup, "og:title") or _meta_content(soup, "twitter:title") or _meta_content(soup, "title")
    summary = _meta_content(soup, "description") or _meta_content(soup, "og:description")
    image_url = _meta_content(soup, "og:image") or _meta_content(soup, "twitter:image")
    published_at = _extract_date_from_json_ld(soup) or _extract_date_near_title(soup) or _extract_date_anywhere(soup)
    return {
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "image_url": (image_url or "").strip() or None,
        "published_at": published_at,
    }


def _fetch_generic_detail_with_retry(url: str, *, source_id: str) -> dict:
    try:
        html = _fetch_html_with_retry(url, source_id=source_id, stage="detail", attempts=3)
    except requests.RequestException:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    title = _meta_content(soup, "og:title") or _meta_content(soup, "twitter:title") or _meta_content(soup, "title")
    summary = _meta_content(soup, "description") or _meta_content(soup, "og:description")
    image_url = _meta_content(soup, "og:image") or _meta_content(soup, "twitter:image")
    published_at = _extract_date_from_json_ld(soup) or _extract_date_near_title(soup) or _extract_date_anywhere(soup)
    return {
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "image_url": (image_url or "").strip() or None,
        "published_at": published_at,
    }


def _meta_content(soup: BeautifulSoup, key: str) -> Optional[str]:
    if key.startswith("og:") or key.startswith("twitter:"):
        tag = soup.find("meta", property=key)
    else:
        tag = soup.find("meta", attrs={"name": key})
    if tag and tag.get("content"):
        return tag["content"]
    return None


def _extract_yozm_list_items(soup: BeautifulSoup, start_url: str) -> list[dict]:
    items = []
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/magazine/detail/" not in href:
            continue
        url = urljoin(start_url, href)
        if url in seen:
            continue
        seen.add(url)
        title = _text_or_alt(a)
        card = a.find_parent()
        summary, published_at, image_url = _extract_card_fields(card, start_url)
        items.append(
            {
                "url": url,
                "title": title,
                "summary": summary,
                "image_url": image_url,
                "published_at": published_at,
            }
        )
    return items


def _extract_iboss_list_items(soup: BeautifulSoup, start_url: str) -> list[dict]:
    items = []
    seen = set()
    pattern = _iboss_article_pattern(start_url)
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if not pattern.search(href):
            continue
        url = urljoin(start_url, href)
        if url in seen:
            continue
        seen.add(url)
        title = _text_or_alt(a)
        card = a.find_parent()
        summary, published_at, image_url = _extract_card_fields(card, start_url)
        items.append(
            {
                "url": url,
                "title": title,
                "summary": summary,
                "image_url": image_url,
                "published_at": published_at,
            }
        )
    return items


def _iboss_article_pattern(start_url: str) -> re.Pattern:
    match = re.search(r"/ab-(\d+)", start_url)
    if match:
        category = match.group(1)
        return re.compile(rf"/ab-{category}-\d+")
    return re.compile(r"/ab-\d+-\d+")


def _text_or_alt(tag) -> str:
    text = tag.get_text(" ", strip=True)
    if text:
        return text
    img = tag.find("img")
    if img and img.get("alt"):
        return img.get("alt").strip()
    return ""


def _extract_card_fields(card, base_url: str) -> tuple[str, Optional[str], Optional[str]]:
    if not card:
        return "", None, None
    text = card.get_text(" ", strip=True)
    summary = _first_non_date_sentence(text)
    published_at = _parse_date_text(text)

    image_url = None
    img = card.find("img")
    if img:
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if src:
            image_url = urljoin(base_url, src)
    return summary, published_at, image_url


def _first_non_date_sentence(text: str) -> str:
    if not text:
        return ""
    parts = [p.strip() for p in re.split(r"[|\u00b7\n]", text) if p.strip()]
    for part in parts:
        if _parse_date_text(part):
            continue
        if len(part) >= 10:
            return part
    return text.strip()


def _extract_date_from_json_ld(soup: BeautifulSoup) -> Optional[str]:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or ""
        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', raw)
        if not match:
            continue
        value = match.group(1)
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.date().isoformat()
        except ValueError:
            continue
    return None
def _extract_date_near_title(soup: BeautifulSoup) -> Optional[str]:
    h1 = soup.find("h1")
    if not h1:
        return None
    container = h1.parent or h1
    text = container.get_text(" ", strip=True)
    return _parse_date_text(text)


def _extract_date_anywhere(soup: BeautifulSoup) -> Optional[str]:
    text = soup.get_text(" ", strip=True)
    return _parse_date_text(text)


def _parse_date_text(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{4})\s*\.\s*(\d{2})\s*\.\s*(\d{2})\s*\.?\b", text)
    if not match:
        return None
    dt = datetime.strptime(f"{match.group(1)}.{match.group(2)}.{match.group(3)}", "%Y.%m.%d")
    return dt.date().isoformat()


def _is_within_days(value: Optional[str], today: date, days: int) -> bool:
    if not value:
        return False
    try:
        published = datetime.fromisoformat(value).date()
    except ValueError:
        try:
            published = datetime.strptime(value, "%Y.%m.%d").date()
        except ValueError:
            return False
    start = today - timedelta(days=days - 1)
    return start <= published <= today


def _normalize_start_urls(start_url) -> list[str]:
    if isinstance(start_url, str):
        url = start_url.strip()
        return [url] if url else []
    if isinstance(start_url, list):
        urls = []
        for value in start_url:
            if not isinstance(value, str):
                continue
            url = value.strip()
            if url:
                urls.append(url)
        return urls
    return []


def _upsert_keyword_article(
    conn,
    keyword: str,
    keyword_norm: str,
    title: str,
    url: str,
    summary: str,
    published_at: Optional[str],
) -> tuple[int, bool]:
    cur = conn.execute("SELECT id FROM keyword_articles WHERE url = ?", (url,))
    row = cur.fetchone()
    if row:
        return row[0], False
    cur = conn.execute(
        """
        INSERT INTO keyword_articles
          (keyword, keyword_norm, title, url, summary, image_url, published_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            keyword,
            keyword_norm,
            title,
            url,
            summary or keyword,
            None,
            published_at,
        ),
    )
    return cur.lastrowid, True


def _bookmark_keyword_article(conn, keyword_article_id: int, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO keyword_bookmarks (keyword_article_id, created_at)
        VALUES (?, ?)
        ON CONFLICT(keyword_article_id) DO UPDATE SET removed_at = NULL
        """,
        (keyword_article_id, created_at),
    )


def _iter_naver_news_items(keyword: str) -> Iterable[dict]:
    url = _naver_news_search_url(keyword)
    try:
        html = _fetch_html(url)
    except requests.RequestException:
        return []
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for a in soup.select("a.news_tit"):
        href = (a.get("href") or "").strip()
        title = (a.get("title") or a.get_text(" ", strip=True) or "").strip()
        if not href or not title:
            continue
        container = a.find_parent()
        text = container.get_text(" ", strip=True) if container else ""
        summary = ""
        summary_tag = container.select_one(".dsc_wrap") if container else None
        if summary_tag:
            summary = summary_tag.get_text(" ", strip=True)
        press = ""
        if container:
            press_tag = container.select_one(".info_group .info")
            if press_tag:
                press = press_tag.get_text(" ", strip=True)
        published_at = _parse_date_text(text) or _parse_relative_date(text)
        items.append(
            {
                "title": title,
                "url": href,
                "summary": summary,
                "published_at": published_at,
                "press": press,
            }
        )
    return items


def _naver_news_search_url(keyword: str) -> str:
    query = quote_plus(keyword)
    return f"https://search.naver.com/search.naver?where=news&query={query}"


def _parse_relative_date(text: str) -> Optional[str]:
    if not text:
        return None
    now = date.today()
    match = re.search(r"(\d+)\s*일\s*전", text)
    if match:
        days = int(match.group(1))
        return (now - timedelta(days=days)).isoformat()
    if "어제" in text:
        return (now - timedelta(days=1)).isoformat()
    if "오늘" in text or "분 전" in text or "시간 전" in text:
        return now.isoformat()
    return None


def _extract_google_press(entry, title: str) -> str:
    source = getattr(entry, "source", None)
    if source and getattr(source, "title", None):
        return str(source.title).strip()
    if " - " in title:
        return title.split(" - ")[-1].strip()
    return ""


def _format_title_with_press(title: str, press: str) -> str:
    clean_title = title.split(" - ")[0].strip()
    if press:
        return f"[{press}] {clean_title}"
    return clean_title


def _normalize_title_for_dedupe(title: str) -> str:
    cleaned = re.sub(r"\[[^\]]+\]\s*", "", title)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().lower()
    return cleaned


def _google_news_rss_url(keyword: str) -> str:
    query = quote_plus(keyword)
    return f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"


def _normalize_google_news_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if "url" in qs and qs["url"]:
            return qs["url"][0]
    except Exception:
        return url
    return url
