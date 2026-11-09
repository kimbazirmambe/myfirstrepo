from django.test import SimpleTestCase
from django.urls import reverse


class HomePageTests(SimpleTestCase):
    def test_home_page_opens_with_navigation(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "dashboard/home.html")
        for label in (
            "Marketing Hub", "Главная", "Источники новостей",
            "Найденные статьи", "Подготовка публикаций",
            "История публикаций", "Настройки",
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)
        self.assertContains(response, 'aria-current="page"')
