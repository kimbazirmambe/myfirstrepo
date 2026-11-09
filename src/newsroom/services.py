"""Bounded offline imports and versioned human editorial decisions."""
from datetime import datetime, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from collectors.podelu_html import extract_articles
from .forms import normalize_url
from .models import Article, Draft, DraftRevision


def parse_date(value):
    if not value:
        return None
    for parser in (lambda v: datetime.fromisoformat(v.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            date = parser(value)
            return date.replace(tzinfo=dt_timezone.utc) if date.tzinfo is None else date
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def import_articles(source, content, format, keywords="", exclude=""):
    if not source.is_active:
        raise ValueError("Источник отключён.")
    if len(content) > 1024 * 1024:
        raise ValueError("Файл слишком большой.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("Сохраните файл в UTF-8.") from error
    rows = []
    if format == "html":
        if urlsplit(source.rss_url).hostname not in {"podelu.by", "www.podelu.by"}:
            raise ValueError("HTML-прототип поддерживает только podelu.by; реальная разметка ещё требует проверки.")
        rows = [(a.title, a.url, "", None) for a in extract_articles(text, source.rss_url)[:100]]
    elif format == "rss":
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise ValueError("DTD и XML-сущности не поддерживаются.")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as error:
            raise ValueError("Не удалось прочитать RSS / Atom XML.") from error
        tag = lambda node: node.tag.rsplit("}", 1)[-1]
        if tag(root) not in {"rss", "feed", "RDF"}:
            raise ValueError("Ожидается файл RSS или Atom.")
        entries = [e for e in root.iter() if tag(e) in {"item", "entry"}][:100]
        for entry in entries:
            values = {}
            url = ""
            for node in entry:
                key = tag(node)
                values.setdefault(key, "".join(node.itertext()))
                if key == "link" and (node.get("rel", "alternate") == "alternate"):
                    url = node.get("href") or (node.text or "")
            rows.append((strip_tags(values.get("title", "")), urljoin(source.rss_url, url.strip()) if url.strip() else "",
                         strip_tags(values.get("description") or values.get("summary") or values.get("content") or "")[:20000],
                         parse_date(values.get("pubDate") or values.get("published") or values.get("date"))))
    else:
        raise ValueError("Неизвестный формат.")
    include = [s.strip().casefold() for s in keywords.split(",") if s.strip()]
    exclude_words = [s.strip().casefold() for s in exclude.split(",") if s.strip()]
    counts = {"added": 0, "duplicates": 0, "filtered": 0, "invalid": 0}
    with transaction.atomic():
        for title, url, body, date in rows:
            if not title.strip() or not url:
                counts["invalid"] += 1
                continue
            try:
                url = normalize_url(url)
            except ValidationError:
                counts["invalid"] += 1
                continue
            haystack = (title + " " + body).casefold()
            if (include and not any(word in haystack for word in include)) or any(word in haystack for word in exclude_words):
                counts["filtered"] += 1
                continue
            _, created = Article.objects.get_or_create(url=url, defaults={
                "title": title.strip()[:300], "body": body, "published_at": date, "source": source,
            })
            counts["added" if created else "duplicates"] += 1
    return counts


def snapshot(draft, actor):
    DraftRevision.objects.create(draft=draft, version=draft.version, subject=draft.subject,
                                 body=draft.body, status=draft.status, actor=actor, automated=actor is None)


class VersionConflict(Exception):
    pass


@transaction.atomic
def update_draft(draft, expected_version, actor, *, changes=None, approve=False):
    if changes is None and not approve:
        raise ValueError("No changes")
    values = dict(changes or {})
    values.update(version=expected_version + 1, updated_at=timezone.now())
    if approve:
        values.update(status=Draft.Status.APPROVED, approved_by=actor, approved_at=timezone.now())
    else:
        values.update(status=Draft.Status.DRAFT, approved_by=None, approved_at=None)
    if Draft.objects.filter(pk=draft.pk, version=expected_version).update(**values) != 1:
        raise VersionConflict("Материал уже изменён. Обновите страницу и повторите действие.")
    draft.refresh_from_db()
    snapshot(draft, actor)
    return draft
