import secrets

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from sources.models import NewsSource


class NewsSourceAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.staff = users.objects.create_user(username="access-staff", is_staff=True)
        cls.regular = users.objects.create_user(username="access-regular")
        cls.inactive = users.objects.create_user(
            username="access-inactive", is_staff=True, is_active=False,
        )
        cls.source = NewsSource.objects.create(
            name="Закрытая учебная лента", rss_url="https://example.com/private-feed.xml",
        )

    def assert_sources_closed(self, client):
        list_url = reverse("sources:list")
        delete_url = reverse("sources:delete", args=[self.source.pk])
        for url in (list_url, delete_url):
            for method in ("get", "post"):
                with self.subTest(url=url, method=method):
                    response = getattr(client, method)(url, {
                        "name": "Запрещённое добавление",
                        "rss_url": "https://example.com/blocked.xml",
                        "is_active": "on",
                    } if method == "post" else {})
                    self.assertRedirects(
                        response, f"{reverse('admin:login')}?next={url}",
                        fetch_redirect_response=False,
                    )
                    self.assertNotContains(response, self.source.name, status_code=302)
                    self.assertEqual(NewsSource.objects.count(), 1)
                    self.assertTrue(NewsSource.objects.filter(pk=self.source.pk).exists())

    def test_anonymous_cannot_view_add_or_delete(self):
        self.assert_sources_closed(self.client)

    def test_non_staff_cannot_view_add_or_delete(self):
        self.client.force_login(self.regular)
        self.assert_sources_closed(self.client)

    def test_inactive_staff_cannot_view_add_or_delete(self):
        self.client.force_login(self.inactive)
        self.assert_sources_closed(self.client)

    def test_revoking_staff_closes_existing_session(self):
        self.client.force_login(self.staff)
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        self.assert_sources_closed(self.client)

    def test_home_is_public_for_anonymous_and_non_staff(self):
        for user in (None, self.regular):
            with self.subTest(user=user):
                client = Client()
                if user is not None:
                    client.force_login(user)
                response = client.get(reverse("home"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Marketing Hub")

    def test_standard_staff_login_returns_to_sources(self):
        password = secrets.token_urlsafe(32)
        self.staff.set_password(password)
        self.staff.save(update_fields=["password"])
        login_url = reverse("admin:login")
        page = self.client.get(login_url, {"next": reverse("sources:list")})
        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "admin/login.html")
        response = self.client.post(login_url, {
            "username": self.staff.username, "password": password,
            "next": reverse("sources:list"),
        })
        self.assertRedirects(response, reverse("sources:list"))
        self.assertContains(self.client.get(reverse("sources:list")), self.source.name)

    def test_standard_login_does_not_admit_non_staff(self):
        password = secrets.token_urlsafe(32)
        self.regular.set_password(password)
        self.regular.save(update_fields=["password"])
        response = self.client.post(reverse("admin:login"), {
            "username": self.regular.username, "password": password,
            "next": reverse("sources:list"),
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        self.assert_sources_closed(self.client)
