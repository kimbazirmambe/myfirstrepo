from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from newsroom.models import Article, Draft, DraftRevision
from newsroom.services import import_articles, snapshot, update_draft, VersionConflict
from sources.models import NewsSource


RSS = b'''<rss version="2.0"><channel><item><title>Marketing news</title><link>https://example.com/news?utm_source=test#part</link><description>&lt;p&gt;Verified fact&lt;/p&gt;</description><pubDate>Mon, 09 Nov 2026 09:00:00 +0000</pubDate></item><item><title>Other topic</title><link>https://example.com/other</link></item></channel></rss>'''


class NewsroomTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(username="editor", is_staff=True)
        cls.source = NewsSource.objects.create(name="Test source", rss_url="https://example.com/feed")

    def setUp(self):
        self.client.force_login(self.staff)
        self.article = Article.objects.create(title="Verified article", url="https://example.com/original", body="A fact to verify")

    def new_draft(self):
        draft = Draft.objects.create(article=self.article, channel="social", body="Original text")
        snapshot(draft, self.staff)
        return draft

    def test_all_editorial_pages_require_staff(self):
        draft = self.new_draft()
        urls = [reverse("newsroom:" + name) for name in ("articles", "article-add", "import", "drafts", "history", "settings")]
        urls += [reverse("newsroom:" + name, args=[self.article.pk]) for name in ("article", "compose")]
        urls += [reverse("newsroom:" + name, args=[draft.pk]) for name in ("draft", "approve", "export")]
        regular = get_user_model().objects.create_user(username="reader")
        for user in (None, regular):
            client = Client()
            if user:
                client.force_login(user)
            for url in urls:
                for method in ("get", "post"):
                    with self.subTest(user=user, url=url, method=method):
                        response = getattr(client, method)(url)
                        self.assertEqual(response.status_code, 302)
                        self.assertTrue(response.url.startswith(reverse("admin:login")))
        self.assertEqual(DraftRevision.objects.count(), 1)
        self.assertEqual(Article.objects.count(), 1)

    def test_manual_article_and_normalized_duplicate(self):
        data = {"title": "New", "url": "https://example.com/new?utm_source=test#fragment", "body": "Notes"}
        response = self.client.post(reverse("newsroom:article-add"), data)
        self.assertEqual(response.status_code, 302)
        saved = Article.objects.get(title="New")
        self.assertEqual(saved.url, "https://example.com/new")
        self.assertIsNone(saved.published_at)
        response = self.client.post(reverse("newsroom:article-add"), data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Article.objects.filter(title="New").count(), 1)

    def test_unsafe_article_url_is_not_saved(self):
        for url in ("javascript:alert(1)", "ftp://example.com/file", "https://user:secret@example.com/item"):
            response = self.client.post(reverse("newsroom:article-add"), {"title": "Bad", "url": url})
            self.assertEqual(response.status_code, 200)
        self.assertFalse(Article.objects.filter(title="Bad").exists())

    def test_rss_import_keywords_dates_and_repeat(self):
        counts = import_articles(self.source, RSS, "rss", "MARKETING")
        self.assertEqual(counts["added"], 1)
        self.assertEqual(counts["filtered"], 1)
        article = Article.objects.get(title="Marketing news")
        self.assertEqual(article.url, "https://example.com/news")
        self.assertEqual(article.body, "Verified fact")
        self.assertEqual(article.published_at.year, 2026)
        self.assertEqual(import_articles(self.source, RSS, "rss", "marketing")["duplicates"], 1)

    def test_exclusions_and_missing_date(self):
        result = import_articles(self.source, RSS, "rss", exclude="marketing")
        self.assertEqual(result["added"], 1)
        self.assertIsNone(Article.objects.get(title="Other topic").published_at)

    def test_atom_import_uses_alternate_link(self):
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom</title><link rel="self" href="https://example.com/api"/><link href="/atom"/><published>2026-11-09T08:00:00Z</published></entry></feed>'
        self.assertEqual(import_articles(self.source, atom, "rss")["added"], 1)
        self.assertEqual(Article.objects.get(title="Atom").url, "https://example.com/atom")

    def test_bad_xml_and_inactive_source_are_rejected(self):
        for body in (b'<!DOCTYPE rss [<!ENTITY a "x">]><rss/>', b'<broken>', b'\xff', b'<html/>'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                import_articles(self.source, body, "rss")
        self.source.is_active = False
        with self.assertRaises(ValueError):
            import_articles(self.source, RSS, "rss")
        self.assertEqual(Article.objects.count(), 1)

    def test_file_import_view_and_size_limit(self):
        response = self.client.post(reverse("newsroom:import"), {
            "source": self.source.pk, "format": "rss", "file": SimpleUploadedFile("feed.xml", RSS),
        })
        self.assertRedirects(response, reverse("newsroom:articles"))
        response = self.client.post(reverse("newsroom:import"), {
            "source": self.source.pk, "format": "rss", "file": SimpleUploadedFile("big.xml", b"a" * (1024 * 1024 + 1)),
        })
        self.assertContains(response, "Максимальный размер файла")

    def test_html_import_is_limited_to_podelu_and_is_repeatable(self):
        html = '<h2><a href="/article/demo">Demo</a></h2>'.encode()
        with self.assertRaises(ValueError):
            import_articles(self.source, html, "html")
        self.source.rss_url = "https://podelu.by/"
        self.assertEqual(import_articles(self.source, html, "html")["added"], 1)
        self.assertEqual(import_articles(self.source, html, "html")["duplicates"], 1)

    def test_search_state_and_escaped_content(self):
        self.article.title = '<script>alert("x")</script>'
        self.article.save()
        url = reverse("newsroom:article", args=[self.article.pk])
        self.assertContains(self.client.get(url), "&lt;script&gt;")
        self.client.post(url, {"state": "selected"})
        self.article.refresh_from_db()
        self.assertEqual(self.article.state, "selected")
        self.assertContains(self.client.get(reverse("newsroom:articles"), {"q": "alert", "state": "selected"}), "&lt;script&gt;")

    def test_create_approve_edit_and_history(self):
        response = self.client.post(reverse("newsroom:compose", args=[self.article.pk]), {
            "channel": "email", "subject": "Subject", "body": "An original email",
        })
        draft = Draft.objects.get()
        self.assertRedirects(response, reverse("newsroom:draft", args=[draft.pk]))
        approve_url = reverse("newsroom:approve", args=[draft.pk])
        self.assertEqual(self.client.get(approve_url).status_code, 405)
        self.client.post(approve_url, {"version": 1})
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.client.post(approve_url, {"version": 1, "reviewed": "on"})
        draft.refresh_from_db()
        self.assertEqual(draft.status, "approved")
        self.assertEqual(draft.approved_by, self.staff)
        self.assertEqual(draft.version, 2)
        self.client.post(reverse("newsroom:draft", args=[draft.pk]), {
            "version": 2, "channel": "email", "subject": "Edited", "body": "Updated text",
        })
        draft.refresh_from_db()
        self.assertEqual(draft.status, "draft")
        self.assertIsNone(draft.approved_at)
        self.assertEqual(draft.version, 3)
        self.assertEqual(draft.revisions.count(), 3)
        self.assertEqual(draft.revisions.get(version=1).body, "An original email")
        self.assertContains(self.client.get(reverse("newsroom:history")), "Edited")

    def test_stale_update_and_approval_cannot_overwrite_new_version(self):
        draft = self.new_draft()
        update_draft(draft, 1, self.staff, changes={"body": "New text"})
        with self.assertRaises(VersionConflict):
            update_draft(draft, 1, self.staff, approve=True)
        response = self.client.post(reverse("newsroom:draft", args=[draft.pk]), {
            "version": 1, "channel": "social", "subject": "Old", "body": "Stale text",
        })
        self.assertEqual(response.status_code, 409)
        draft.refresh_from_db()
        self.assertEqual(draft.body, "New text")
        self.assertEqual(draft.revisions.count(), 2)

    def test_email_requires_subject(self):
        response = self.client.post(reverse("newsroom:compose", args=[self.article.pk]), {"channel": "email", "body": "Body"})
        self.assertContains(response, "Укажите тему email")
        self.assertFalse(Draft.objects.exists())

    def test_export_always_contains_source_and_status(self):
        draft = self.new_draft()
        response = self.client.get(reverse("newsroom:export", args=[draft.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.article.url, response.content.decode())
        self.assertIn("Черновик", response.content.decode())
        self.assertIn("attachment", response["Content-Disposition"])

    def test_source_deletion_keeps_article_and_history(self):
        self.article.source = self.source
        self.article.save()
        draft = self.new_draft()
        self.source.delete()
        self.article.refresh_from_db()
        self.assertIsNone(self.article.source)
        self.assertTrue(DraftRevision.objects.filter(draft=draft).exists())

    def test_csrf_blocks_editorial_writes(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        draft = self.new_draft()
        for url in (reverse("newsroom:article-add"), reverse("newsroom:import"), reverse("newsroom:compose", args=[self.article.pk]), reverse("newsroom:approve", args=[draft.pk])):
            self.assertEqual(client.post(url).status_code, 403)
