"""Bounded HTTP collection. Run outside PythonAnywhere's web worker."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ipaddress
import re
import socket
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from bs4 import BeautifulSoup
import feedparser
from protego import Protego

USER_AGENT = "MarketingHubBot/1.0"
MAX_BYTES = 2 * 1024 * 1024


class CollectionError(Exception):
    pass


def public_url(url):
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.port not in {None, 80, 443}:
            raise ValueError
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise CollectionError("Разрешены только общедоступные HTTP/HTTPS-адреса без учётных данных.") from None
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Fetcher:
    def __init__(self):
        self.opener = build_opener(NoRedirect)
        self.rules = {}
        self.last_request = {}

    def _raw(self, url):
        url = public_url(url)
        origin = urlsplit(url).netloc
        rules = self.rules.get((urlsplit(url).scheme, origin))
        delay = max(1.0, float(rules.crawl_delay(USER_AGENT) or 0)) if rules else 1.0
        if delay > 10:
            raise CollectionError("Сайт требует интервал запросов более 10 секунд; автоматическая проверка остановлена.")
        remaining = delay - (time.monotonic() - self.last_request.get(origin, 0))
        if remaining > 0:
            time.sleep(remaining)
        self.last_request[origin] = time.monotonic()
        try:
            with self.opener.open(Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xml,application/rss+xml,application/atom+xml"}), timeout=10) as response:
                body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise CollectionError("Ответ сайта превышает лимит 2 МБ.")
                return body, response.headers.get_content_charset() or "utf-8"
        except HTTPError:
            raise
        except OSError:
            raise CollectionError("Сетевая ошибка или тайм-аут при обращении к источнику.") from None

    def _robots(self, url):
        parts = urlsplit(url)
        key = (parts.scheme, parts.netloc)
        if key not in self.rules:
            robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
            try:
                body, encoding = self._raw(robots_url)
            except HTTPError as error:
                if error.code == 404:
                    body, encoding = b"", "utf-8"
                else:
                    raise CollectionError(f"robots.txt недоступен (HTTP {error.code}); сбор остановлен.") from None
            self.rules[key] = Protego.parse(body.decode(encoding, errors="replace"))
        if not self.rules[key].can_fetch(url, USER_AGENT):
            raise CollectionError("Этот путь запрещён правилами robots.txt.")

    def get(self, url):
        for _ in range(4):
            url = public_url(url)
            self._robots(url)
            try:
                body, encoding = self._raw(url)
                text = body.decode(encoding, errors="replace")
                if re.search(r'<title>\s*(?:just a moment|captcha|access denied)', text, re.I):
                    raise CollectionError("Сайт требует проверку доступа; обход не выполняется.")
                return text, url
            except HTTPError as error:
                if error.code in {301, 302, 303, 307, 308} and error.headers.get("Location"):
                    url = urljoin(url, error.headers["Location"])
                    continue
                raise CollectionError(f"Источник ответил HTTP {error.code}; ограничения не обходятся.") from None
        raise CollectionError("Слишком много перенаправлений.")


def clean_text(value, limit=1200):
    return " ".join(BeautifulSoup(str(value or ""), "html.parser").get_text(" ", strip=True).split())[:limit]


def date_string(value):
    if not value:
        return None
    for parser in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            result = parser(value)
            if result.tzinfo is None:
                result = result.replace(tzinfo=timezone.utc)
            return result.isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    return None


def parse_feed(text, base_url):
    if re.match(r"\s*(?:<!doctype\s+html\b|<html\b)", text, re.I):
        return None
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise CollectionError("DTD и XML-сущности в ленте не поддерживаются.")
    feed = feedparser.parse(text)
    if not feed.version:
        return None
    results = []
    for entry in feed.entries[:20]:
        if not entry.get("title") or not entry.get("link"):
            continue
        url = urljoin(base_url, entry.link)
        try:
            parts = urlsplit(url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None or len(url) > 2048:
                continue
        except ValueError:
            continue
        results.append({"title": clean_text(entry.title, 300), "url": url,
                        "body": clean_text(entry.get("summary", "")),
                        "published_at": date_string(entry.get("published"))})
    return results


def article_links(text, base_url):
    soup = BeautifulSoup(text, "html.parser")
    result = []
    for link in soup.select("a[href]"):
        if link.find_parent(["nav", "footer", "aside"]):
            continue
        url = urljoin(base_url, link["href"])
        parts = urlsplit(url)
        if parts.hostname != urlsplit(base_url).hostname or parts.path in {"", "/"}:
            continue
        if re.search(r"/(?:admin|api|login|register|search|rubric|tag|category)(?:/|$)", parts.path):
            continue
        title = clean_text(link.get_text(" ", strip=True), 300)
        if len(title) < 12:
            continue
        heading = link.find(["h1", "h2", "h3", "h4"]) or link.find_parent(["h1", "h2", "h3", "h4"])
        article_path = re.search(r"/(?:article|articles|news|blog)/[^/]+", parts.path)
        if not (heading or article_path):
            continue
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        if not any(item[1] == url for item in result):
            result.append((title, url))
    return result[:30]


def metadata(text, url, fallback_title):
    soup = BeautifulSoup(text, "html.parser")
    title = soup.find("h1")
    description = soup.find("meta", attrs={"property": "og:description"}) or soup.find("meta", attrs={"name": "description"})
    date = soup.find("meta", attrs={"property": "article:published_time"})
    if date is None:
        date = soup.find("time", attrs={"datetime": True})
    return {"title": clean_text(title.get_text(" ", strip=True), 300) if title else fallback_title,
            "url": url, "body": clean_text(description.get("content", "")) if description else "",
            "published_at": date_string(date.get("content") or date.get("datetime")) if date else None}


def collect_source(url, fetcher=None, known_urls=()):
    fetcher = fetcher or Fetcher()
    text, final_url = fetcher.get(url)
    items = parse_feed(text, final_url)
    if items is not None:
        return items
    soup = BeautifulSoup(text, "html.parser")
    for link in soup.select('link[rel="alternate"][href]'):
        if link.get("type") not in {"application/rss+xml", "application/atom+xml"}:
            continue
        feed_url = urljoin(final_url, link["href"])
        if urlsplit(feed_url).hostname != urlsplit(final_url).hostname:
            continue
        feed_text, feed_url = fetcher.get(feed_url)
        items = parse_feed(feed_text, feed_url)
        if items is not None:
            return items
        break
    items = []
    links = [(title, link) for title, link in article_links(text, final_url) if link not in known_urls]
    if not links and article_links(text, final_url):
        return []
    for title, article_url in links[:3]:
        article_text, article_url = fetcher.get(article_url)
        items.append(metadata(article_text, article_url, title))
    if not items:
        raise CollectionError("В HTML не найдены статьи. Возможно, нужен отдельный обработчик этого сайта или RSS.")
    return items
