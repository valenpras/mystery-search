"""Fetch multi-gallery (tag-filtered only) and gallery articles; write JSONL."""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, Tag

BASE = "https://unsolved.com"
MULTI_GALLERY_PATH = "/multi-gallery/"
DEFAULT_DELAY = 1.5
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_BASE = 2.0

# Allowed archive_tag slugs (sanity check after nav parse)
EXPECTED_TAGS = frozenset(
    {
        "ghosts",
        "legends",
        "lost-loves",
        "missing",
        "murder",
        "psychic",
        "science",
        "solved",
        "treasure",
        "ufo",
        "unexplained-death",
        "wanted",
    }
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_user_agent() -> str:
    contact = os.environ.get("UNSOLVED_CRAWL_CONTACT", "").strip()
    if not contact:
        contact = "set-UNSOLVED_CRAWL_CONTACT-env"
    return f"MysterySearchCourseCrawler/0.1 (educational; contact: {contact})"


def normalize_article_url(url: str) -> str:
    p = urlparse(urljoin(BASE, url))
    path = p.path.rstrip("/") + "/"
    return f"{p.scheme or 'https'}://{p.netloc}{path}"


def is_allowed_listing_url(url: str) -> bool:
    """Reject bare /multi-gallery/ and /multi-gallery/page/N/ without ?tag=."""
    p = urlparse(urljoin(BASE, url))
    if p.netloc and "unsolved.com" not in p.netloc:
        return False
    q = parse_qs(p.query)
    if "tag" not in q or not (q["tag"] and q["tag"][0].strip()):
        return False
    path = (p.path or "").rstrip("/") or "/"
    if path == "/multi-gallery":
        return True
    if re.match(r"^/multi-gallery/page/\d+$", path):
        return True
    return False


def archive_tag_from_listing_url(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    return (q.get("tag") or [""])[0].strip()


def dedupe_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (
        record["article_url"],
        record["archive_tag"],
        record["listing_page_url"],
    )


def load_done_keys(cases_path: Path) -> set[tuple[str, str, str]]:
    done: set[tuple[str, str, str]] = set()
    if not cases_path.is_file():
        return done
    with cases_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                done.add(dedupe_key(obj))
            except json.JSONDecodeError:
                continue
    return done


class PoliteSession:
    def __init__(self, delay: float, timeout: float) -> None:
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": build_user_agent()})

    def get(self, url: str) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = BACKOFF_BASE ** attempt
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                time.sleep(self.delay)
                return r
            except (requests.RequestException, OSError) as e:
                last_exc = e
                time.sleep(BACKOFF_BASE ** attempt)
        assert last_exc is not None
        raise last_exc


def parse_archive_tags(soup: BeautifulSoup) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for a in soup.select('a[href*="tag="]'):
        href = a.get("href") or ""
        if "/multi-gallery/" not in href and "multi-gallery" not in href:
            continue
        full = urljoin(BASE, href)
        if not is_allowed_listing_url(full):
            continue
        tag = archive_tag_from_listing_url(full)
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    unknown = [t for t in out if t not in EXPECTED_TAGS]
    if unknown:
        # Still crawl unknown tags but surface in stderr via caller if needed
        pass
    return out


def parse_listing_gallery_urls(soup: BeautifulSoup, archive_tag: str) -> list[str]:
    """Prefer case tiles with rel=gallery-{tag}; fallback to any /gallery/ link."""
    rel_prefix = f"gallery-{archive_tag}"
    anchors = soup.select(f'a[rel^="{rel_prefix}"][href*="/gallery/"]')
    if not anchors:
        anchors = soup.select('a[href*="/gallery/"]')
    urls: list[str] = []
    seen: set[str] = set()
    for a in anchors:
        href = a.get("href")
        if not href:
            continue
        full = urljoin(BASE, href).split("#")[0]
        if "/gallery/" not in full:
            continue
        if "unsolved.com" not in urlparse(full).netloc:
            continue
        u = normalize_article_url(full)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def parse_listing_pagination_hrefs(soup: BeautifulSoup) -> list[str]:
    hrefs: list[str] = []
    for a in soup.select("ul.pagination a[href]"):
        h = (a.get("href") or "").strip()
        if not h or h == "#":
            continue
        full = urljoin(BASE, h)
        if is_allowed_listing_url(full):
            hrefs.append(full)
    return hrefs


def crawl_all_listing_urls_for_tag(session: PoliteSession, archive_tag: str) -> list[str]:
    """Return ordered unique listing page URLs for one archive_tag."""
    start = f"{BASE}/multi-gallery/?tag={archive_tag}"
    visited: set[str] = set()
    queue: deque[str] = deque([start])
    ordered: list[str] = []
    while queue:
        u = queue.popleft()
        nu = urljoin(BASE, u)
        if nu in visited or not is_allowed_listing_url(nu):
            continue
        visited.add(nu)
        ordered.append(nu)
        r = session.get(nu)
        soup = BeautifulSoup(r.text, "html.parser")
        for nxt in parse_listing_pagination_hrefs(soup):
            if nxt not in visited and is_allowed_listing_url(nxt):
                queue.append(nxt)
    return ordered


def gallery_category_from_article_soup(soup: BeautifulSoup) -> str | None:
    art = soup.select_one("article[id^='post-'].gallery")
    if not art or not isinstance(art, Tag):
        return None
    classes = art.get("class") or []
    for c in classes:
        if isinstance(c, str) and c.startswith("gallery_category-"):
            return c.replace("gallery_category-", "", 1)
    return None


def _strip_scripts_styles(root: Tag) -> None:
    for t in root.find_all(["script", "style", "noscript"]):
        t.decompose()


def html_to_readable_text(soup_fragment: Tag) -> str:
    clone = BeautifulSoup(str(soup_fragment), "html.parser")
    root = clone.find()
    if not root or not isinstance(root, Tag):
        return ""
    _strip_scripts_styles(root)
    text = root.get_text(separator="\n", strip=True)
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines() if ln.strip()]
    return "\n\n".join(lines)


def _img_url_from_tag(img: Tag) -> str | None:
    src = (img.get("src") or "").strip()
    srcset = (img.get("srcset") or "").strip()
    if src:
        return urljoin(BASE, src)
    if srcset:
        # "url 370w, url 500w" -> take first URL
        part = srcset.split(",")[0].strip().split()[0]
        if part:
            return urljoin(BASE, part)
    return None


def extract_content_images(entry: Tag) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for img in entry.find_all("img"):
        if not isinstance(img, Tag):
            continue
        url = _img_url_from_tag(img)
        if not url:
            continue
        alt = (img.get("alt") or "").strip()
        caption = ""
        cap = img.find_parent("div", class_=lambda x: x and "wp-caption" in x)
        if cap and isinstance(cap, Tag):
            ct = cap.select_one(".wp-caption-text")
            if ct:
                caption = ct.get_text(separator=" ", strip=True)
        if url not in seen_urls:
            seen_urls.add(url)
            out.append({"url": url, "alt": alt, "caption": caption})
    return out


def extract_comments(soup: BeautifulSoup) -> list[dict[str, str]]:
    block = soup.select_one("div.comments, .comments")
    if not block or not isinstance(block, Tag):
        return []
    out: list[dict[str, str]] = []
    for li in block.select("ol.comment-list > li.comment, li.comment"):
        if not isinstance(li, Tag):
            continue
        cite = li.select_one(".comment-author cite, cite.fn")
        author = cite.get_text(strip=True) if cite else ""
        time_el = li.select_one("time[datetime]")
        date_iso = ""
        date_raw = ""
        if time_el and isinstance(time_el, Tag):
            date_iso = (time_el.get("datetime") or "").strip()
            date_raw = time_el.get_text(strip=True)
        body = li.select_one(".media-body")
        text_parts: list[str] = []
        if body and isinstance(body, Tag):
            for p in body.find_all("p", recursive=False):
                if isinstance(p, Tag) and "reply" in (p.get("class") or []):
                    continue
                t = p.get_text(separator=" ", strip=True)
                if t:
                    text_parts.append(t)
        text = "\n\n".join(text_parts)
        if not text.strip():
            continue
        out.append(
            {
                "author": author,
                "date_iso": date_iso,
                "date_raw": date_raw,
                "text": text,
            }
        )
    return out


def parse_article(
    soup: BeautifulSoup,
    listing_page_url: str,
    archive_tag: str,
    article_url: str,
) -> dict[str, Any]:
    title_el = soup.select_one("section h1, .container h1, h1")
    title = title_el.get_text(strip=True) if title_el else ""

    entry = soup.select_one(".entry-content")
    if not entry or not isinstance(entry, Tag):
        raise ValueError("missing .entry-content")

    body_html = entry.decode_contents()
    body_text = html_to_readable_text(entry)

    gc = gallery_category_from_article_soup(soup)
    content_images = extract_content_images(entry)
    comments = extract_comments(soup)

    return {
        "article_url": normalize_article_url(article_url),
        "listing_page_url": listing_page_url,
        "archive_tag": archive_tag,
        "gallery_category": gc or "",
        "title": title,
        "body_html": body_html,
        "body_text": body_text,
        "content_images": content_images,
        "comments": comments,
        "fetched_at": _utc_now_iso(),
    }


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def append_failure(path: Path, url: str, context: str, reason: str) -> None:
    append_jsonl(
        path,
        {
            "url": url,
            "context": context,
            "reason": reason,
            "ts": _utc_now_iso(),
        },
    )


@dataclass
class CrawlConfig:
    output_dir: Path
    delay: float = DEFAULT_DELAY
    timeout: float = DEFAULT_TIMEOUT
    limit_articles: int | None = None
    tags: list[str] | None = None


def run_crawl(cfg: CrawlConfig) -> None:
    out_dir = cfg.output_dir.resolve()
    cases_path = out_dir / "cases.jsonl"
    failed_path = out_dir / "failed_urls.jsonl"

    done = load_done_keys(cases_path)
    session = PoliteSession(cfg.delay, cfg.timeout)

    tags = cfg.tags
    if not tags:
        seed = os.environ.get("UNSOLVED_CRAWL_SEED_TAG", "ghosts").strip() or "ghosts"
        r = session.get(f"{BASE}/multi-gallery/?tag={seed}")
        tags = parse_archive_tags(BeautifulSoup(r.text, "html.parser"))
        if not tags:
            tags = sorted(EXPECTED_TAGS)

    articles_written = 0
    for archive_tag in tags:
        try:
            listing_pages = crawl_all_listing_urls_for_tag(session, archive_tag)
        except Exception as e:
            append_failure(
                failed_path,
                f"{BASE}/multi-gallery/?tag={archive_tag}",
                "listing_tag_crawl",
                repr(e),
            )
            continue

        for listing_url in listing_pages:
            listing_final = listing_url
            try:
                r = session.get(listing_url)
                listing_final = r.url
                soup = BeautifulSoup(r.text, "html.parser")
                tag_from_page = archive_tag_from_listing_url(listing_final)
                gallery_urls = parse_listing_gallery_urls(soup, tag_from_page)
            except Exception as e:
                append_failure(failed_path, listing_url, "listing_page", repr(e))
                continue

            for article_url in gallery_urls:
                rec_key = (
                    normalize_article_url(article_url),
                    tag_from_page,
                    listing_final,
                )
                if rec_key in done:
                    continue

                try:
                    ar = session.get(article_url)
                    final_article = ar.url
                    asoup = BeautifulSoup(ar.text, "html.parser")
                    record = parse_article(
                        asoup,
                        listing_final,
                        tag_from_page,
                        final_article,
                    )
                except Exception as e:
                    append_failure(failed_path, article_url, "article", repr(e))
                    continue

                append_jsonl(cases_path, record)
                done.add(dedupe_key(record))
                articles_written += 1
                if cfg.limit_articles is not None and articles_written >= cfg.limit_articles:
                    return


def main(argv: list[str] | None = None) -> None:
    import argparse

    p = argparse.ArgumentParser(
        description="Crawl unsolved.com gallery (tag-filtered archive only).",
        epilog="Environment: UNSOLVED_CRAWL_CONTACT=your@university.edu (shown in User-Agent). "
        "Optional: UNSOLVED_CRAWL_SEED_TAG=ghosts when discovering tags from nav.",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "unsolved",
        help="Directory for cases.jsonl and failed_urls.jsonl",
    )
    p.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds to sleep after each successful response")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    p.add_argument(
        "--limit-articles",
        type=int,
        default=None,
        help="Stop after writing this many new article records (for testing)",
    )
    p.add_argument(
        "--tags",
        nargs="*",
        default=None,
        help="If set, only crawl these archive_tag slugs (e.g. ghosts murder). Default: all from nav.",
    )
    args = p.parse_args(argv)

    run_crawl(
        CrawlConfig(
            output_dir=args.output_dir,
            delay=args.delay,
            timeout=args.timeout,
            limit_articles=args.limit_articles,
            tags=args.tags,
        )
    )


if __name__ == "__main__":
    main()
