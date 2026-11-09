"""Idempotent ingestion and explicitly labelled factual draft templates."""
from django.db import transaction
from django.utils import timezone

from .forms import normalize_url
from .models import Article, Draft
from .services import parse_date, snapshot


def automatic_drafts(article, generated=None):
    if Article.objects.filter(pk=article.pk, auto_prepared=False).update(auto_prepared=True) != 1:
        return 0
    # Brief attributed quotation, not an invented AI summary or a full copy.
    fact = " ".join(article.body.split())
    if fact:
        fragment = fact[:180] + ("…" if len(fact) > 180 else "")
        evidence = f"В описании первоисточника: «{fragment}»\n\n"
    else:
        evidence = "Описание недоступно. Перед утверждением прочитайте оригинал: подробности не домысливались.\n\n"
    texts = {
        "social": f"Тема для обсуждения: {article.title}\n\n{evidence}Что в этой новости важно для вашей команды?\n\nИсточник: {article.url}",
        "messenger": f"Обратите внимание: {article.title}\n\n{evidence}Подробнее: {article.url}",
        "email": f"Здравствуйте!\n\nВ подборку вошёл материал: {article.title}\n\n{evidence}Познакомьтесь с оригиналом и оцените, относится ли эта тема к вашим задачам.\n\nИсточник: {article.url}",
    }
    generated_map = {draft["channel"]: draft for draft in generated} if generated else {}
    created = 0
    for channel, body in texts.items():
        if channel in generated_map:
            body = generated_map[channel]["body"] + "\n\nИсточник: " + article.url
        if article.drafts.filter(channel=channel).exists():
            continue
        draft = Draft.objects.create(article=article, channel=channel, subject=generated_map[channel]["subject"] if generated_map else article.title[:200],
                                     body=body, generation_method="ai" if generated_map else "factual_template")
        snapshot(draft, None)
        created += 1
    return created


@transaction.atomic
def ingest(source, rows, error=""):
    counts = {"added": 0, "duplicates": 0, "filtered": 0, "drafts": 0, "writing_errors": 0}
    if not error:
        include = [w.strip().casefold() for w in source.keywords.split(",") if w.strip()]
        exclude = [w.strip().casefold() for w in source.exclude_keywords.split(",") if w.strip()]
        for row in rows:
            title = row["title"].strip()
            url = normalize_url(row["url"])
            body = row.get("body", "")
            text = (title + " " + body).casefold()
            if (include and not any(w in text for w in include)) or any(w in text for w in exclude):
                counts["filtered"] += 1
                continue
            article, created = Article.objects.get_or_create(url=url, defaults={
                "source": source, "title": title, "body": body,
                "published_at": parse_date(row.get("published_at")),
            })
            counts["added" if created else "duplicates"] += 1
            if row.get("writing_error"):
                counts["writing_errors"] += 1
            else:
                counts["drafts"] += automatic_drafts(article, row.get("drafts"))
    source.last_checked_at = timezone.now()
    source.collection_requested = False
    source.collection_error = error[:500] if error else ("Статьи найдены, но ИИ не подготовил черновики. Проверьте подключение провайдера и запросите повторную проверку." if counts["writing_errors"] else "")
    source.last_added_count = counts["added"]
    source.save(update_fields=["last_checked_at", "collection_requested", "collection_error", "last_added_count"])
    return counts
