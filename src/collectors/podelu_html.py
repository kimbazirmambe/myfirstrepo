"""Extract article candidates from local HTML; podelu.by markup is unverified.

Only heading-associated links are considered. This is a conservative generic
heuristic, not a confirmed adapter for the live website. No network I/O occurs.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import urljoin, urlsplit, urlunsplit

ALLOWED_HOSTS = {"podelu.by", "www.podelu.by"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
IGNORED = {"nav", "aside", "footer", "script", "style", "template", "noscript"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


@dataclass(frozen=True)
class ArticleCandidate:
    title: str
    url: str


class _HeadingLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.links: list[str | None] = []
        self.heading: str | None = None
        self.text: list[str] = []
        self.hrefs: list[str] = []
        self.candidates: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID_TAGS:
            self.stack.append(tag)
        if tag == "a":
            href = dict(attrs).get("href")
            self.links.append(href)
            if self.heading is not None and href:
                self.hrefs.append(href)
        if tag in HEADINGS and not IGNORED.intersection(self.stack):
            self.heading = tag
            self.text = []
            self.hrefs = [href for href in self.links if href]

    def handle_endtag(self, tag):
        if tag == self.heading:
            self.candidates.append((" ".join("".join(self.text).split()), self.hrefs))
            self.heading = None
        if tag == "a" and self.links:
            self.links.pop()
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, data):
        if self.heading is not None and not IGNORED.intersection(self.stack):
            self.text.append(data)


def _article_url(href: str, base_url: str) -> str | None:
    href = href.strip()
    if not href or href.startswith(("#", "?")):
        return None
    try:
        parts = urlsplit(urljoin(base_url, href))
        if parts.scheme not in {"http", "https"} or parts.hostname not in ALLOWED_HOSTS:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        if parts.port not in {None, 80, 443} or parts.path in {"", "/"}:
            return None
        # Keep query strings: their meaning on the real site has not been checked.
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, parts.query, ""))
    except ValueError:
        return None


def extract_articles(html: str, base_url: str = "https://podelu.by/") -> list[ArticleCandidate]:
    """Return ordered, distinct candidates, without verifying or fetching links.

    Use the original page URL as base_url when parsing relative links. HTML
    <base> tags cannot change it. Dates and article bodies are not extracted.
    """
    base = urlsplit(base_url)
    if base.scheme not in {"http", "https"} or base.hostname not in ALLOWED_HOSTS:
        raise ValueError("base_url must be an HTTP(S) page on podelu.by")
    parser = _HeadingLinks()
    parser.feed(html)
    parser.close()
    result = []
    seen = set()
    for title, hrefs in parser.candidates:
        urls = {_article_url(href, base_url) for href in hrefs}
        urls.discard(None)
        if not title or len(urls) != 1:
            continue
        url = urls.pop()
        if url not in seen:
            result.append(ArticleCandidate(title=title, url=url))
            seen.add(url)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html_file", type=Path, help="Local UTF-8 HTML file; URLs are not downloaded")
    parser.add_argument("--base-url", default="https://podelu.by/", help="Original page URL for relative links")
    args = parser.parse_args()
    try:
        html = args.html_file.read_text(encoding="utf-8-sig")
        articles = extract_articles(html, args.base_url)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps([asdict(article) for article in articles], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
