#!/usr/bin/env python3
"""Crawl pages on www.musashino-u.ac.jp and save URL/title pairs as CSV.

Usage: python3 scrape_site.py --delay 1.0 --output sitemap.csv
Every HTTP request is preceded by time.sleep(delay) to reduce server load.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import posixpath
import signal
import time
from collections import deque
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


START_URL = "https://www.musashino-u.ac.jp/"
ALLOWED_HOST = "www.musashino-u.ac.jp"
USER_AGENT = "EducationalSiteMapCrawler/1.0 (polite; contact: student project)"
NON_HTML_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico",
    ".zip", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css",
    ".js", ".xml", ".rss", ".mp3", ".mp4", ".mov", ".avi", ".woff", ".woff2",
}


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_map = dict(attrs)
        if tag.lower() == "title":
            self.in_title = True
        elif tag.lower() == "a" and attrs_map.get("href"):
            self.links.append(attrs_map["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return " ".join(" ".join(self.title_parts).split())


def normalize_url(url: str) -> str | None:
    url, _fragment = urldefrag(url)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or parts.hostname != ALLOWED_HOST:
        return None
    if parts.username or parts.password:
        return None
    # Normalize host/scheme and discard fragments; retain query strings because
    # they can identify distinct HTML pages on the site.
    raw_path = parts.path or "/"
    path = posixpath.normpath(raw_path)
    if raw_path.endswith("/") and not path.endswith("/"):
        path += "/"
    if not path.startswith("/"):
        path = "/" + path
    path = quote(path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
    return urlunsplit(("https", ALLOWED_HOST, path, query, ""))


def is_html_candidate(url: str) -> bool:
    suffix = posixpath.splitext(urlsplit(url).path.lower())[1]
    return suffix not in NON_HTML_EXTENSIONS


def fetch_html(url: str, delay: float, timeout: float) -> bytes | None:
    time.sleep(delay)
    request = Request(url, headers={"User-Agent": USER_AGENT})
    previous_handler = signal.getsignal(signal.SIGALRM)

    def alarm_handler(_signum, _frame):
        raise TimeoutError(f"request exceeded {timeout} seconds")

    try:
        signal.signal(signal.SIGALRM, alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout)
        with urlopen(request, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"text/html", "application/xhtml+xml"}:
                return None
            chunks: list[bytes] = []
            while True:
                try:
                    chunk = response.read1(64 * 1024)
                except (TimeoutError, OSError):
                    break
                if not chunk:
                    break
                chunks.append(chunk)
                if sum(map(len, chunks)) >= 4 * 1024 * 1024:
                    break
            return b"".join(chunks)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        print(f"Skip {url}: {exc}")
        return None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def crawl(
    start_url: str,
    delay: float,
    timeout: float,
    max_pages: int,
    output_path: str,
    resume: bool,
) -> dict[str, str]:
    start = normalize_url(start_url)
    if start is None:
        raise ValueError("Start URL must be on www.musashino-u.ac.jp")
    pages: dict[str, str] = {}
    queue_path = output_path + ".queue.json"
    if resume and os.path.exists(output_path):
        with open(output_path, encoding="utf-8-sig", newline="") as csvfile:
            for row in csv.DictReader(csvfile):
                normalized = normalize_url(row.get("URL", ""))
                if normalized:
                    pages[normalized] = row.get("title", "")
    if resume and os.path.exists(queue_path):
        with open(queue_path, encoding="utf-8") as queuefile:
            pending = json.load(queuefile)
        queue = deque(
            normalized
            for url in pending
            if (normalized := normalize_url(url)) and is_html_candidate(normalized)
        )
    else:
        queue = deque(pages.keys() if pages else [start])
    queued = set(pages) | set(queue)
    processed_since_checkpoint = 0

    while queue and len(pages) < max_pages:
        url = queue.popleft()
        processed_since_checkpoint += 1
        body = fetch_html(url, delay, timeout)
        if body is None:
            if processed_since_checkpoint >= 50:
                save_csv(pages, output_path)
                save_queue(queue, queue_path)
                processed_since_checkpoint = 0
            continue
        parser = PageParser()
        try:
            parser.feed(body.decode("utf-8", errors="replace"))
        except Exception as exc:  # malformed markup should not stop the crawl
            print(f"Could not parse {url}: {exc}")
            continue
        is_new_page = url not in pages
        pages[url] = parser.title
        print(f"[{len(pages)}] {url} — {parser.title}")
        if is_new_page and len(pages) % 50 == 0:
            save_csv(pages, output_path)
        for href in parser.links:
            candidate = normalize_url(urljoin(url, href))
            if candidate and is_html_candidate(candidate) and candidate not in queued:
                queued.add(candidate)
                queue.append(candidate)
        if processed_since_checkpoint >= 50:
            save_csv(pages, output_path)
            save_queue(queue, queue_path)
            processed_since_checkpoint = 0
    if queue:
        print(f"Reached --max-pages={max_pages}; {len(queue)} URLs remain unvisited.")
        save_queue(queue, queue_path)
    else:
        try:
            os.remove(queue_path)
        except FileNotFoundError:
            pass
    return pages


def save_csv(pages: dict[str, str], output_path: str) -> None:
    with open(output_path, "w", encoding="utf-8-sig", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["URL", "title"])
        writer.writerows(pages.items())


def save_queue(queue: deque[str], queue_path: str) -> None:
    temporary_path = queue_path + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as queuefile:
        json.dump(list(queue), queuefile, ensure_ascii=False)
    os.replace(temporary_path, queue_path)


def main() -> None:
    argp = argparse.ArgumentParser(description=__doc__)
    argp.add_argument("--start-url", default=START_URL)
    argp.add_argument("--output", default="sitemap.csv")
    argp.add_argument("--delay", type=float, default=1.0, help="seconds to sleep before every request")
    argp.add_argument("--timeout", type=float, default=20.0)
    argp.add_argument("--max-pages", type=int, default=2000)
    argp.add_argument("--resume", action="store_true", help="resume from the CSV and saved URL queue")
    args = argp.parse_args()
    if args.delay < 0 or args.max_pages < 1:
        argp.error("--delay must be nonnegative and --max-pages must be positive")

    pages = crawl(args.start_url, args.delay, args.timeout, args.max_pages, args.output, args.resume)
    save_csv(pages, args.output)
    print(f"Saved {len(pages)} pages to {args.output}")


if __name__ == "__main__":
    main()
