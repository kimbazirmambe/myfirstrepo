"""Bounded archive/generation worker. No Django or database on the runner."""
import os
import time

from .archive import discover_archive, read_public_article
from .web import CollectionError, Fetcher, SiteAccessError
from .writing import WritingError, generate_post


def run_jobs(call, max_batches=6, budget_seconds=240, fetcher=None):
    fetcher = fetcher or Fetcher()
    started = time.monotonic()
    searches = 0
    generations = 0
    for _ in range(max_batches + 1):
        if time.monotonic() - started > budget_seconds:
            break
        queue = call('/collector/jobs/')
        search, generation = queue.get('search'), queue.get('generation')
        if not search and not generation:
            break
        if generation and generations < 3:
            payload = {'kind': 'generation', 'id': generation['id']}
            try:
                # Check key first: no pointless source requests when AI is absent.
                if not os.environ.get('MARKETING_GEMINI_KEY', '').strip():
                    payload['blocked'] = True
                    raise WritingError('Бесплатный ИИ не подключён. Сохраните ключ Google AI Studio (проект без платного биллинга) в GitHub Actions Secret MARKETING_GEMINI_KEY и повторите запрос.')
                row = read_public_article(generation['url'], fetcher)
                row['requested_url'] = generation['url']
                payload.update(generate_post(row, generation['channel'], generation['editorial_name']))
                payload['article'] = row
            except (CollectionError, WritingError) as error:
                payload['error'] = str(error)[:500]
            call('/collector/job-results/', payload)
            generations += 1
        if search and searches < max_batches:
            payload = {'kind': 'search', 'id': search['id'], 'cursor': search['cursor']}
            try:
                if search['discover']:
                    urls, limited = discover_archive(search['source_url'], search['query'], fetcher)
                    payload.update(urls=urls, limited=limited)
                else:
                    rows = []
                    for url in search['urls']:
                        try:
                            row = read_public_article(url, fetcher)
                        except SiteAccessError:
                            raise
                        except CollectionError as error:
                            row = {'error': str(error)[:500]}
                        row['requested_url'] = url
                        rows.append(row)
                    payload['rows'] = rows
                    searches += 1
            except CollectionError as error:
                payload['error'] = str(error)[:500]
            call('/collector/job-results/', payload)
        elif not generation or generations >= 3:
            break
    return {'batches': searches, 'generations': generations}
