import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from collectors.podelu_html import ArticleCandidate, extract_articles

FIXTURE = ROOT / "tests/fixtures/podelu_sample.html"


class PodeluHtmlTests(unittest.TestCase):
    def test_synthetic_fixture_extracts_titles_links_and_removes_exact_duplicates(self):
        with patch("socket.socket", side_effect=AssertionError("Network access forbidden")):
            results = extract_articles(FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(results, [
            ArticleCandidate("Учебная новость & пример", "https://podelu.by/articles/demo-one/"),
            ArticleCandidate("Вторая учебная новость", "https://podelu.by/articles/demo-two/"),
        ])

    def test_relative_urls_use_page_location_and_ignore_html_base_tag(self):
        html = '<base href="https://example.com/"><h2><a href="story/">Новость</a></h2>'
        self.assertEqual(extract_articles(html, "https://podelu.by/news/"), [
            ArticleCandidate("Новость", "https://podelu.by/news/story/"),
        ])

    def test_external_unsafe_empty_and_homepage_links_are_skipped(self):
        for href in (
            "", "#section", "?page=2", "/", "mailto:test@example.com",
            "ftp://podelu.by/story/", "https://podelu.by.evil.example/story/",
            "https://example.com/story/", "//example.com/story/",
            "https://user:password@podelu.by/story/", "https://podelu.by:9000/story/",
            "http://[broken/", "https://podelu.by:bad/story/",
        ):
            with self.subTest(href=href):
                self.assertEqual(extract_articles(f'<h2><a href="{href}">Новость</a></h2>'), [])

    def test_empty_text_ambiguous_links_and_non_headings_are_skipped(self):
        html = '''<h2><a href="/one/"></a></h2>
        <h2><a href="/one/">Одна</a><a href="/two/">Другая</a></h2>
        <a href="/three/">Без заголовка</a>'''
        self.assertEqual(extract_articles(html), [])

    def test_script_and_template_text_are_not_extracted(self):
        html = '''<template><h2><a href="/template/">Шаблон</a></h2></template>
        <script>"<h2><a href='/script/'>Скрипт</a></h2>"</script>
        <h2><a href="/real/">Текст<script>ignored()</script></a></h2>'''
        self.assertEqual(extract_articles(html), [ArticleCandidate("Текст", "https://podelu.by/real/")])

    def test_query_is_preserved_and_www_is_allowed(self):
        self.assertEqual(extract_articles('<h2><a href="https://www.podelu.by/story/?id=2#part">Новость</a></h2>'), [
            ArticleCandidate("Новость", "https://www.podelu.by/story/?id=2"),
        ])

    def test_empty_document_returns_no_candidates(self):
        self.assertEqual(extract_articles(""), [])

    def test_other_base_domains_are_rejected(self):
        with self.assertRaises(ValueError):
            extract_articles("", "https://example.com/")

    def test_cli_reads_local_fixture_and_prints_json(self):
        run = subprocess.run(
            [sys.executable, str(ROOT / "src/collectors/podelu_html.py"), str(FIXTURE)],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=True,
        )
        self.assertEqual(len(json.loads(run.stdout)), 2)
        self.assertEqual(json.loads(run.stdout)[0]["title"], "Учебная новость & пример")

    def test_cli_missing_file_fails_without_success_result(self):
        run = subprocess.run(
            [sys.executable, str(ROOT / "src/collectors/podelu_html.py"), str(FIXTURE.parent / "missing.html")],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
        self.assertNotEqual(run.returncode, 0)
        self.assertEqual(run.stdout, "")
