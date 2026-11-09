from django.core.validators import URLValidator
from django.db import models


class NewsSource(models.Model):
    name = models.CharField("Название", max_length=200)
    rss_url = models.URLField(
        "URL RSS-ленты", max_length=2048,
        validators=[URLValidator(schemes=["http", "https"])],
    )
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Дата добавления", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Источник новостей"
        verbose_name_plural = "Источники новостей"

    def __str__(self):
        return self.name
