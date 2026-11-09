from django.core.validators import URLValidator
from django.db import models


class NewsSource(models.Model):
    name = models.CharField("Название", max_length=200)
    rss_url = models.URLField(
        "URL RSS-ленты", max_length=2048,
        validators=[URLValidator(schemes=["http", "https"])],
    )
    is_active = models.BooleanField("Активен", default=True)
    keywords = models.CharField("Ключевые слова через запятую", max_length=500, blank=True)
    exclude_keywords = models.CharField("Исключающие слова через запятую", max_length=500, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    collection_requested = models.BooleanField(default=True)
    collection_error = models.CharField(max_length=500, blank=True)
    seen_urls = models.JSONField(default=list, blank=True)
    last_filtered_count = models.PositiveIntegerField(default=0)
    last_added_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField("Дата добавления", auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "Источник новостей"
        verbose_name_plural = "Источники новостей"

    def __str__(self):
        return self.name
