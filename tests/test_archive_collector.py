import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from collectors.archive import discover_archive, matches_query, read_public_article
from collectors.web import CollectionError
from collectors.writing import WritingError, generate_post
from collectors.job_runner import run_jobs


class ArchiveTests(unittest.TestCase):
    def test_russian_word_forms_and_and_or_groups(self):
        self.assertTrue(matches_query('Процедура банкротства компании', 'банкротство'))
        self.assertTrue(matches_query('При банкротстве компании', 'банкротства'))
        self.assertFalse(matches_query('Банки открывают новые офисы', 'банкротства'))
        self.assertTrue(matches_query('Налоговая проверка', 'банкротство, налоговые проверки'))
        self.assertFalse(matches_query('Налоговая служба', 'налоговые проверки'))
        self.assertFalse(matches_query('abc', ', '))

    def test_public_sitemap_limits_to_same_site_articles_and_prioritizes(self):
        fetcher = Mock()
        urls = ['https://podelu.by/article/aaa', 'https://podelu.by/article/bankrotstvo',
                'https://podelu.by/search', 'https://other.example/article/1']
        xml = '<urlset>' + ''.join(f'<url><loc>{u}</loc></url>' for u in urls) + '</urlset>'
        fetcher.get.side_effect = [('<html/>', 'https://podelu.by/'), (xml, 'https://podelu.by/sitemap.xml')]
        found, limited = discover_archive('https://podelu.by/', 'банкротства', fetcher)
        self.assertEqual(found, [urls[1], urls[0]])
        self.assertFalse(limited)
        self.assertEqual(fetcher.get.call_count, 2)

    def test_xml_entities_and_site_errors_stop_discovery(self):
        fetcher = Mock()
        fetcher.get.side_effect = [('', 'https://example.com/'), ('<!DOCTYPE urlset><urlset/>', 'https://example.com/sitemap.xml')]
        with self.assertRaises(CollectionError):
            discover_archive('https://example.com/', 'test', fetcher)
        fetcher.get.side_effect = CollectionError('robots.txt forbids')
        with self.assertRaises(CollectionError):
            discover_archive('https://example.com/', 'test', fetcher)

    def test_text_excludes_sidebar_toc_and_retains_publication_date(self):
        body = 'Банкротство предприятия: проверенные факты. ' * 10
        html = '<main><h1>Заголовок</h1><div class="min-w-0"><div><p>Автор</p></div><div><div class="summary-block"><p>Оглавление</p></div><p>' + body + '</p></div></div><aside>НЕ ИЗ СТАТЬИ</aside></main><script type="application/ld+json">{"@graph":[{"@type":"Article","datePublished":"2026-11-01T10:00:00Z"}]}</script>'
        fetcher = Mock(); fetcher.get.return_value = (html, 'https://podelu.by/article/1')
        row = read_public_article('https://podelu.by/article/1', fetcher)
        self.assertIn(body.strip(), row['content_text'])
        self.assertNotIn('Оглавление', row['content_text'])
        self.assertNotIn('НЕ ИЗ СТАТЬИ', row['content_text'])
        self.assertNotIn('Автор', row['content_text'])
        self.assertTrue(row['published_at'].startswith('2026-11-01'))
        self.assertTrue(row['content_complete'])

    def test_paywall_and_missing_article_body_fail_closed(self):
        fetcher = Mock()
        for html in ('<main><p>No article</p></main>', '<article><div class="paywall">subscription</div></article>',
                     '<script type="application/ld+json">{"isAccessibleForFree":false}</script>'):
            fetcher.get.return_value = (html, 'https://example.com/article/1')
            with self.assertRaises(CollectionError):
                read_public_article('https://example.com/article/1', fetcher)

    def test_generation_without_key_is_blocked_without_fetching_article(self):
        call = Mock(side_effect=[{'generation': {'id': 5, 'url': 'https://example.com/article/1'}}, {}, {}])
        with patch.dict(os.environ, {'MARKETING_GEMINI_KEY': ''}), patch('collectors.job_runner.read_public_article') as fetch:
            run_jobs(call, max_batches=1)
        fetch.assert_not_called()
        payload = call.call_args_list[1].args[1]
        self.assertTrue(payload['blocked'])
        self.assertIn('MARKETING_GEMINI_KEY', payload['error'])


class GeminiTests(unittest.TestCase):
    article = {'title': 'Факты', 'url': 'https://example.com/a', 'content_text': 'Факт. ' * 100, 'content_complete': True}

    @patch.dict(os.environ, {'MARKETING_GEMINI_KEY': 'test-not-a-real-key'})
    @patch('collectors.writing.build_opener')
    def test_one_format_uses_full_article_and_validates_json(self, opener):
        data = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps({'subject': 'Тема', 'body': 'Новый редакционный текст'})}]}}]}
        opener.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(data).encode()
        draft = generate_post(self.article, 'email', 'Редакция')
        self.assertEqual(draft['subject'], 'Тема')
        request = opener.return_value.open.call_args.args[0]
        self.assertNotIn('test-not-a-real-key', request.full_url)
        payload = json.loads(request.data)
        self.assertIn(self.article['content_text'], payload['contents'][0]['parts'][0]['text'])
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict(os.environ, {'MARKETING_GEMINI_KEY': 'test-not-a-real-key'})
    @patch('collectors.writing.build_opener')
    def test_quota_failure_is_safe_and_never_falls_back(self, opener):
        opener.return_value.open.side_effect = HTTPError('https://provider/', 429, 'SECRET RESPONSE', {}, None)
        with self.assertRaises(WritingError) as caught:
            generate_post(self.article, 'social', 'Редакция')
        self.assertIn('квота', str(caught.exception))
        self.assertNotIn('SECRET', str(caught.exception))
        self.assertEqual(opener.return_value.open.call_count, 1)

    @patch.dict(os.environ, {'MARKETING_GEMINI_KEY': ''})
    def test_no_key_and_incomplete_text_never_generate_fake_drafts(self):
        with self.assertRaises(WritingError):
            generate_post(self.article, 'social', 'Редакция')
        with patch.dict(os.environ, {'MARKETING_GEMINI_KEY': 'test-only'}), self.assertRaises(WritingError):
            generate_post({**self.article, 'content_complete': False}, 'social', 'Редакция')


class SiteRefusalTests(unittest.TestCase):
    def test_429_stops_other_pages_without_immediate_retries(self):
        from collectors.web import Fetcher, SiteAccessError
        fetcher = Fetcher()
        error = HTTPError('https://example.com/a', 429, 'limit', {'Retry-After': '60'}, None)
        with patch('collectors.web.public_url', side_effect=lambda url: url), patch.object(fetcher, '_robots'), patch.object(fetcher, '_raw', side_effect=error) as raw:
            for url in ('https://example.com/a', 'https://example.com/b'):
                with self.assertRaises(SiteAccessError):
                    fetcher.get(url)
            self.assertEqual(raw.call_count, 1)

    def test_archive_host_refusal_stops_job_without_advancing_cursor(self):
        from collectors.web import SiteAccessError
        queue = {'search': {'id': 2, 'source_url': 'https://example.com/', 'query': 'test',
                            'cursor': 0, 'discover': False, 'urls': ['https://example.com/a', 'https://example.com/b']}}
        call = Mock(side_effect=[queue, {}, {}])
        with patch('collectors.job_runner.read_public_article', side_effect=SiteAccessError('HTTP 429; stopped')) as reader:
            run_jobs(call, max_batches=1)
        self.assertEqual(reader.call_count, 1)
        payload = call.call_args_list[1].args[1]
        self.assertIn('error', payload)
        self.assertNotIn('rows', payload)
        self.assertEqual(payload['cursor'], 0)


class GeminiDiagnosticsTests(unittest.TestCase):
    def test_invalid_key_classification_never_echoes_provider_secrets(self):
        import io
        from collectors.writing import gemini_error
        private = 'private-value-that-must-not-be-logged'
        body = json.dumps({'error': {'status': 'INVALID_ARGUMENT', 'message': 'API key not valid: ' + private,
            'details': [{'reason': 'API_KEY_INVALID', 'metadata': {'key': private}}]}}).encode()
        error = HTTPError('https://example.com/' + private, 400, private, {}, io.BytesIO(body))
        result = gemini_error(error)
        self.assertIn('HTTP 400 INVALID_ARGUMENT API_KEY_INVALID', result)
        self.assertNotIn(private, result)
