"""Staff-controlled archive searches and generation, consumed by an external worker."""
import json
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from collectors.archive import matches_query, same_site
from .collector_api import collector_auth
from .forms import normalize_url
from .models import ArchiveSearch, Article, Draft, GenerationRequest
from .services import parse_date, snapshot


@require_GET
@collector_auth
def queue(request):
    search = ArchiveSearch.objects.filter(status__in=['queued', 'running'], source__is_active=True).order_by('created_at').first()
    generation = GenerationRequest.objects.filter(status='queued').select_related('article').order_by('created_at').first()
    return JsonResponse({
        'search': {'id': search.pk, 'source_url': search.source_url, 'query': search.query,
                   'discover': search.status == 'queued', 'cursor': search.cursor,
                   'urls': search.urls[search.cursor:search.cursor + 10]} if search else None,
        'generation': {'id': generation.pk, 'url': generation.article.url, 'channel': generation.channel,
                       'editorial_name': generation.editorial_name} if generation else None,
    })


def string(value, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError
    return value


def article_row(row, expected_url):
    if not isinstance(row, dict) or row.get('requested_url') != expected_url:
        raise ValueError
    if row.get('error'):
        string(row['error'], 500)
        return
    normalize_url(string(row.get('url'), 2048, True))
    if not same_site(row['url'], expected_url):
        raise ValueError
    string(row.get('title'), 300, True)
    string(row.get('body', ''), 1200)
    string(row.get('content_text'), 30000, True)
    if len(row['content_text']) < 200:
        raise ValueError
    if type(row.get('content_complete')) is not bool:
        raise ValueError
    if row.get('published_at') is not None:
        string(row['published_at'], 100)


def save_article(row, source=None):
    article, _ = Article.objects.get_or_create(url=normalize_url(row['url']), defaults={
        'source': source, 'title': row['title'], 'body': row.get('body', ''),
        'published_at': parse_date(row.get('published_at')),
    })
    # Preserve editor notes, manually corrected titles and previous drafts.
    article.content_text = row['content_text']
    article.content_complete = row['content_complete']
    article.content_fetched_at = timezone.now()
    article.save(update_fields=['content_text', 'content_complete', 'content_fetched_at'])
    return article


@csrf_exempt
@require_POST
@collector_auth
@transaction.atomic
def receive(request):
    if len(request.body) > 2 * 1024 * 1024:
        return JsonResponse({'error': 'Payload too large'}, status=413)
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict) or type(data.get('id')) is not int:
            raise ValueError
        if data.get('kind') == 'search':
            job = ArchiveSearch.objects.get(pk=data['id'], source__is_active=True)
            if job.status not in {'queued', 'running'} or type(data.get('cursor')) is not int or job.cursor != data['cursor']:
                return JsonResponse({'error': 'Stale job'}, status=409)
            if data.get('error'):
                job.status, job.error = 'failed', string(data['error'], 500)
            elif job.status == 'queued':
                urls = data.get('urls')
                if not isinstance(urls, list) or not 1 <= len(urls) <= 1000 or type(data.get('limited')) is not bool:
                    raise ValueError
                for url in urls:
                    normalize_url(string(url, 2048, True))
                    if not same_site(url, job.source_url):
                        raise ValueError
                job.urls, job.status, job.limited = list(dict.fromkeys(urls)), 'running', data['limited']
            else:
                rows = data.get('rows')
                expected = job.urls[job.cursor:job.cursor + 10]
                if not isinstance(rows, list) or len(rows) != len(expected) or not expected:
                    raise ValueError
                # Validate the entire batch before writing anything.
                for row, url in zip(rows, expected):
                    article_row(row, url)
                for row in rows:
                    if row.get('error'):
                        job.error_count += 1
                        job.error = row['error']
                        continue
                    if not row['content_complete']:
                        job.limited = True
                    if matches_query(row['title'] + ' ' + row['content_text'], job.query) and not (job.source.exclude_keywords.strip() and matches_query(row['title'] + ' ' + row['content_text'], job.source.exclude_keywords)):
                        article = save_article(row, job.source)
                        job.articles.add(article)
                job.cursor += len(rows)
                job.matched_count = job.articles.count()
                if job.cursor == len(job.urls):
                    job.status = 'partial' if job.error_count or job.limited else 'completed'
            job.save()
        elif data.get('kind') == 'generation':
            job = GenerationRequest.objects.select_related('article').get(pk=data['id'])
            if job.status != 'queued':
                return JsonResponse({'error': 'Stale job'}, status=409)
            if data.get('error'):
                job.error = string(data['error'], 500)
                job.status = 'blocked' if data.get('blocked') is True else 'failed'
            else:
                row = data.get('article')
                article_row(row, job.article.url)
                if row.get('error') or not row['content_complete']:
                    raise ValueError
                subject = string(data.get('subject'), 200, job.channel == 'email')
                body = string(data.get('body'), 10000, True)
                # Attribution is added by the app, never trusted to the model.
                host = urlsplit(job.article.url).hostname
                body += f'\n\nПо материалам {host}\nИсточник: {job.article.url}'
                save_article(row)
                draft = Draft.objects.create(article=job.article, channel=job.channel, subject=subject,
                                             body=body, generation_method='ai')
                snapshot(draft, None)
                job.draft, job.status = draft, 'completed'
            job.save()
        else:
            raise ValueError
    except (ValueError, TypeError, KeyError, ValidationError):
        return JsonResponse({'error': 'Invalid job payload'}, status=400)
    except (ArchiveSearch.DoesNotExist, GenerationRequest.DoesNotExist):
        return JsonResponse({'error': 'Job missing or inactive'}, status=404)
    return JsonResponse({'status': job.status})
