import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import run_collector
from collectors.web import CollectionError, Fetcher, article_links, collect_source, metadata, parse_feed, public_url


class WebCollectorTests(unittest.TestCase):
    def test_private_local_and_credential_urls_are_rejected(self):
        for url in ('file:///etc/passwd', 'http://localhost/', 'https://user:pass@example.com/', 'http://example.com:8000/'):
            with self.subTest(url=url), patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 80))]), self.assertRaises(CollectionError):
                public_url(url)

    def test_mixed_public_private_dns_is_rejected(self):
        addresses = [(2, 1, 6, '', ('8.8.8.8', 443)), (2, 1, 6, '', ('10.0.0.1', 443))]
        with patch('socket.getaddrinfo', return_value=addresses), self.assertRaises(CollectionError):
            public_url('https://example.com/')

    def test_public_url_preserves_query_and_drops_fragment(self):
        with patch('socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]):
            self.assertEqual(public_url('https://EXAMPLE.com/news?q=one#part'), 'https://example.com/news?q=one')

    def test_feed_metadata_without_following_article_links(self):
        xml = '<rss version="2.0"><channel><item><title>News title</title><link>https://example.com/article/1</link><description>Fact</description><pubDate>Mon, 09 Nov 2026 09:00:00 +0000</pubDate></item></channel></rss>'
        feed = parse_feed(xml, 'https://example.com/feed')
        self.assertEqual(feed[0]['title'], 'News title')
        self.assertTrue(feed[0]['published_at'].startswith('2026-11-09'))
        self.assertIsNone(parse_feed('<!DOCTYPE html><html><body>Home</body></html>', 'https://example.com/'))
        with self.assertRaises(CollectionError):
            parse_feed('<!DOCTYPE rss><rss/>', 'https://example.com/')

    def test_html_article_links_exclude_navigation_and_duplicates(self):
        html = '''<!DOCTYPE html><nav><a href="/article/no">Do not collect this</a></nav>
        <a href="/article/one">A useful article title</a><a href="/article/one">A useful article title</a>
        <h2><a href="/posts/two">Another useful headline</a></h2>
        <a href="https://external.example/article/no">An external article</a>'''
        self.assertEqual(len(article_links(html, 'https://example.com/')), 2)

    def test_collect_html_then_skip_known_articles(self):
        class FakeFetcher:
            def __init__(self):
                self.calls = []
            def get(self, url):
                self.calls.append(url)
                if url.endswith('/'):
                    return '<!DOCTYPE html><a href="/article/one">A useful article title</a>', url
                return '<h1>Actual headline</h1><meta name="description" content="A fact"><time datetime="2026-11-09T09:00:00Z">date</time>', url
        fetcher = FakeFetcher()
        result = collect_source('https://example.com/', fetcher)
        self.assertEqual(result[0]['title'], 'Actual headline')
        self.assertEqual(result[0]['body'], 'A fact')
        self.assertEqual(len(fetcher.calls), 2)
        self.assertEqual(collect_source('https://example.com/', FakeFetcher(), known_urls=['https://example.com/article/one']), [])

    def test_robots_denial_stops_before_page_request(self):
        fetcher = Fetcher()
        with patch('collectors.web.public_url', side_effect=lambda url: url), patch.object(fetcher, '_raw', return_value=('User-agent: *\nDisallow: /private*'.encode(), 'utf-8')) as raw:
            with self.assertRaises(CollectionError):
                fetcher.get('https://example.com/private/page')
            self.assertEqual(raw.call_count, 1)

    def test_robots_failure_is_not_silently_ignored(self):
        fetcher = Fetcher()
        error = HTTPError('https://example.com/robots.txt', 403, 'Forbidden', {}, None)
        with patch('collectors.web.public_url', side_effect=lambda url: url), patch.object(fetcher, '_raw', side_effect=error):
            with self.assertRaises(CollectionError):
                fetcher.get('https://example.com/')

    def test_redirect_to_private_address_is_revalidated(self):
        fetcher = Fetcher()
        redirect = HTTPError('https://example.com/', 302, 'Found', {'Location': 'http://127.0.0.1/'}, None)
        def validate(url):
            if '127.0.0.1' in url:
                raise CollectionError('Private address')
            return url
        with patch('collectors.web.public_url', side_effect=validate), patch.object(fetcher, '_robots'), patch.object(fetcher, '_raw', side_effect=redirect):
            with self.assertRaises(CollectionError):
                fetcher.get('https://example.com/')

    def test_metadata_missing_date_does_not_invent_it(self):
        row = metadata('<h1>Headline</h1>', 'https://example.com/article', 'Fallback')
        self.assertIsNone(row['published_at'])
        self.assertEqual(row['body'], '')


class CollectorRunnerTests(unittest.TestCase):
    def test_no_credentials_means_no_requests(self):
        with patch.dict(os.environ, {"COLLECTOR_TOKEN": "", "HUB_BASE_URL": ""}), patch.object(run_collector, "hub_call") as hub, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_collector.main(), 1)
            hub.assert_not_called()

    def test_one_failed_source_is_reported_and_next_source_runs(self):
        source_rows = [{"id": 1, "url": "https://example.com/one"}, {"id": 2, "url": "https://example.com/two"}]
        with patch.dict(os.environ, {"COLLECTOR_TOKEN": "test-only-" + "x" * 40, "HUB_BASE_URL": "https://example.pythonanywhere.com"}), patch.object(run_collector, "hub_call", side_effect=[{"sources": source_rows}, {"added": 0, "drafts": 0}, {"added": 1, "drafts": 3}]) as hub, patch.object(run_collector, "collect_source", side_effect=[CollectionError("robots denied"), [{"title": "News", "url": "https://example.com/news"}]]) as collect, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_collector.main(), 0)
            self.assertEqual(collect.call_count, 2)
            self.assertEqual(hub.call_args_list[1].args[3]["error"], "robots denied")
            self.assertEqual(len(hub.call_args_list[2].args[3]["articles"]), 1)
