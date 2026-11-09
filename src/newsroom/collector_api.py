"""Narrow machine endpoints. They never approve or publish drafts."""
from datetime import timedelta
from functools import wraps
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from sources.models import NewsSource
from .automation import ingest
from .forms import normalize_url


def collector_auth(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        token = settings.COLLECTOR_TOKEN
        if len(token) < 32:
            return JsonResponse({"error": "Collector is not configured"}, status=503)
        if not constant_time_compare(request.headers.get("Authorization", ""), "Bearer " + token):
            return JsonResponse({"error": "Unauthorized"}, status=401)
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        return response
    return wrapper


@require_GET
@collector_auth
def queue(request):
    due = timezone.now() - timedelta(hours=1)
    sources = NewsSource.objects.filter(is_active=True).filter(
        Q(collection_requested=True) | Q(last_checked_at__isnull=True) | Q(last_checked_at__lt=due)
    ).order_by("last_checked_at", "pk")[:5]
    return JsonResponse({"sources": [
        {"id": source.pk, "url": source.rss_url,
         "known_urls": list(dict.fromkeys(source.seen_urls + list(source.article_set.filter(auto_prepared=True).order_by("-discovered_at").values_list("url", flat=True)[:50])))[:50]}
        for source in sources
    ]})


@csrf_exempt
@require_POST
@collector_auth
def receive(request):
    if len(request.body) > 1024 * 1024:
        return JsonResponse({"error": "Payload too large"}, status=413)
    try:
        payload = json.loads(request.body)
        if not isinstance(payload, dict) or type(payload.get("source_id")) is not int:
            raise ValueError
        source = NewsSource.objects.get(pk=payload["source_id"], is_active=True)
        if payload.get("source_url") != source.rss_url:
            return JsonResponse({"error": "Source changed; refresh queue"}, status=409)
        rows = payload.get("articles", [])
        error = payload.get("error", "")
        if not isinstance(rows, list) or len(rows) > 20 or not isinstance(error, str) or len(error) > 500:
            raise ValueError
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError
            for field, limit in (("title", 300), ("url", 2048), ("body", 1200)):
                value = row.get(field, "")
                if not isinstance(value, str) or len(value) > limit:
                    raise ValueError
            if not row.get("title", "").strip() or not row.get("url"):
                raise ValueError
            if row.get("published_at") is not None and not isinstance(row["published_at"], str):
                raise ValueError
            normalize_url(row["url"])
            writing_error = row.get("writing_error", "")
            if not isinstance(writing_error, str) or len(writing_error) > 500:
                raise ValueError
            drafts = row.get("drafts")
            if drafts is not None:
                if not isinstance(drafts, list) or len(drafts) != 3 or not all(isinstance(d, dict) for d in drafts):
                    raise ValueError
                if {d.get("channel") for d in drafts} != {"social", "messenger", "email"}:
                    raise ValueError
                for draft in drafts:
                    if not isinstance(draft.get("subject"), str) or len(draft["subject"]) > 200:
                        raise ValueError
                    if not isinstance(draft.get("body"), str) or not draft["body"].strip() or len(draft["body"]) > 17000:
                        raise ValueError
                    if draft["channel"] == "email" and not draft["subject"].strip():
                        raise ValueError
    except NewsSource.DoesNotExist:
        return JsonResponse({"error": "Source inactive or missing"}, status=404)
    except (ValueError, TypeError, ValidationError):
        return JsonResponse({"error": "Invalid payload"}, status=400)
    return JsonResponse(ingest(source, rows, error))
