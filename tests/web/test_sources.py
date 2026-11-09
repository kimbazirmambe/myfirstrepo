from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from sources.models import NewsSource


class NewsSourceTests(TestCase):
    def setUp(self):
        self.list_url = reverse("sources:list")
        self.valid_data = {
            "name": "Учебная лента",
            "rss_url": "https://example.com/feed.xml",
            "is_active": "on",
        }

    def create_source(self, **kwargs):
        return NewsSource.objects.create(**{
            "name": "Учебная лента", "rss_url": "https://example.com/feed.xml", **kwargs,
        })

    def test_empty_list_and_navigation_from_home(self):
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пока нет источников")
        self.assertContains(response, "Добавить источник")
        self.assertContains(response, f'href="{self.list_url}" aria-current="page"')
        self.assertContains(self.client.get(reverse("home")), f'href="{self.list_url}"')

    def test_add_persists_values_and_date_then_redirects(self):
        response = self.client.post(self.list_url, self.valid_data)
        self.assertRedirects(response, self.list_url)
        source = NewsSource.objects.get()
        self.assertEqual(source.name, self.valid_data["name"])
        self.assertEqual(source.rss_url, self.valid_data["rss_url"])
        self.assertTrue(source.is_active)
        self.assertIsNotNone(source.created_at)
        self.assertEqual(str(source), source.name)
        page = self.client.get(self.list_url)
        self.assertContains(page, source.name)
        self.assertContains(page, source.rss_url)
        self.assertContains(page, "Активен")
        self.assertContains(page, "Добавлен")
        self.assertEqual(NewsSource.objects.count(), 1)

    def test_unchecked_activity_is_saved_as_inactive(self):
        data = {k: v for k, v in self.valid_data.items() if k != "is_active"}
        response = self.client.post(self.list_url, data)
        self.assertRedirects(response, self.list_url)
        self.assertFalse(NewsSource.objects.get().is_active)
        self.assertContains(self.client.get(self.list_url), "Неактивен")

    def test_invalid_urls_are_not_saved(self):
        for url in ("not-a-url", "https://", "javascript:alert(1)", "ftp://example.com/feed", ""):
            with self.subTest(url=url):
                response = self.client.post(self.list_url, {**self.valid_data, "rss_url": url})
                self.assertEqual(response.status_code, 200)
                self.assertFormError(response.context["form"], "rss_url", response.context["form"].errors["rss_url"])
                self.assertFalse(NewsSource.objects.exists())
                self.assertContains(response, self.valid_data["name"])

    def test_name_is_required_and_limited(self):
        for name in ("", "   ", "a" * 201):
            with self.subTest(name=name):
                response = self.client.post(self.list_url, {**self.valid_data, "name": name})
                self.assertEqual(response.status_code, 200)
                self.assertIn("name", response.context["form"].errors)
                self.assertFalse(NewsSource.objects.exists())

    def test_model_rejects_non_http_url(self):
        with self.assertRaises(ValidationError):
            NewsSource(name="Учебная лента", rss_url="ftp://example.com/feed").full_clean()

    def test_user_text_is_escaped_in_list_and_confirmation(self):
        source = self.create_source(name='<script>alert("x")</script>')
        for url in (self.list_url, reverse("sources:delete", args=[source.pk])):
            response = self.client.get(url)
            self.assertNotContains(response, '<script>alert("x")</script>')
            self.assertContains(response, "&lt;script&gt;")

    def test_delete_get_only_confirms_and_post_removes_selected_source(self):
        source = self.create_source()
        retained = self.create_source(name="Оставить")
        url = reverse("sources:delete", args=[source.pk])
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Удалить источник?")
        self.assertContains(page, "Отмена")
        self.assertTrue(NewsSource.objects.filter(pk=source.pk).exists())
        self.assertRedirects(self.client.post(url), self.list_url)
        self.assertFalse(NewsSource.objects.filter(pk=source.pk).exists())
        self.assertTrue(NewsSource.objects.filter(pk=retained.pk).exists())

    def test_missing_source_returns_404(self):
        url = reverse("sources:delete", args=[999])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url).status_code, 404)

    def test_csrf_protects_add_and_delete(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(self.list_url, self.valid_data).status_code, 403)
        self.assertFalse(NewsSource.objects.exists())
        client.get(self.list_url)
        token = client.cookies["csrftoken"].value
        data = {**self.valid_data, "csrfmiddlewaretoken": token}
        self.assertRedirects(client.post(self.list_url, data), self.list_url)
        source = NewsSource.objects.get()
        url = reverse("sources:delete", args=[source.pk])
        self.assertEqual(client.post(url).status_code, 403)
        self.assertTrue(NewsSource.objects.filter(pk=source.pk).exists())
        self.assertRedirects(client.post(url, {"csrfmiddlewaretoken": token}), self.list_url)
        self.assertFalse(NewsSource.objects.exists())

    def test_other_methods_cannot_change_sources(self):
        source = self.create_source()
        for url in (self.list_url, reverse("sources:delete", args=[source.pk])):
            self.assertEqual(self.client.delete(url).status_code, 405)
        self.assertEqual(NewsSource.objects.count(), 1)
