from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup


def fetch_html(url: str, timeout: int = 20) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yong2-local/0.1)"}
    session = requests.Session()
    session.trust_env = False
    resp = session.get(
        url,
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.text


def meta_content(soup: BeautifulSoup, key: str) -> Optional[str]:
    if key.startswith("og:") or key.startswith("twitter:"):
        tag = soup.find("meta", property=key)
    else:
        tag = soup.find("meta", attrs={"name": key})
    if tag and tag.get("content"):
        return str(tag.get("content")).strip()
    return None


def parse_date(text: str) -> Optional[str]:
    match = re.search(r"\b(\d{4})\s*\.\s*(\d{2})\s*\.\s*(\d{2})\b", text or "")
    if not match:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _iboss_article_pattern(start_url: str) -> re.Pattern:
    match = re.search(r"/ab-(\d+)", start_url)
    if match:
        category = match.group(1)
        return re.compile(rf"/ab-{category}-\d+")
    return re.compile(r"/ab-\d+-\d+")


def extract_links(list_html: str, base_url: str, limit: int) -> list[str]:
    soup = BeautifulSoup(list_html, "html.parser")
    seen = set()
    links: list[str] = []
    pattern = _iboss_article_pattern(base_url)
    for a in soup.select("a[href]"):
        href = str(a.get("href") or "").strip()
        if not pattern.search(href):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= limit:
            break
    return links


def extract_article(url: str) -> dict:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = (
        meta_content(soup, "og:title")
        or meta_content(soup, "twitter:title")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "")
    )
    summary = meta_content(soup, "description") or meta_content(soup, "og:description") or ""
    image_url = meta_content(soup, "og:image") or meta_content(soup, "twitter:image")
    published_at = parse_date(soup.get_text(" ", strip=True))

    return {
        "source_id": "i_boss",
        "title": (title or "").strip(),
        "url": url,
        "summary": (summary or "").strip() or "i-boss article",
        "image_url": (image_url or "").strip() or None,
        "published_at": published_at,
    }


def run(start_url: str, out_path: Path, limit: int) -> int:
    list_html = fetch_html(start_url)
    links = extract_links(list_html, start_url, limit=limit)

    items = []
    for link in links:
        try:
            item = extract_article(link)
        except Exception:
            continue
        if not item["title"] or not item["url"]:
            continue
        items.append(item)

    payload = {
        "generated_at": datetime.utcnow().isoformat(),
        "source_id": "i_boss",
        "start_url": start_url,
        "articles": items,
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(items)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export i-boss articles to a JSON seed file.")
    parser.add_argument("--start-url", default="https://www.i-boss.co.kr/ab-7214")
    parser.add_argument("--out", default="data/iboss_manual.json")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    count = run(args.start_url, Path(args.out), max(1, args.limit))
    print(f"saved {count} articles -> {args.out}")


if __name__ == "__main__":
    main()
