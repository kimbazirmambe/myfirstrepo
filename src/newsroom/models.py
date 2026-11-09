from django.conf import settings
from django.db import models


class Article(models.Model):
    class State(models.TextChoices):
        NEW = "new", "Новая"
        SELECTED = "selected", "Отобрана"
        REJECTED = "rejected", "Отклонена"

    source = models.ForeignKey("sources.NewsSource", null=True, blank=True, on_delete=models.SET_NULL)
    title = models.CharField("Заголовок", max_length=300)
    url = models.URLField("Оригинал", max_length=2048, unique=True)
    body = models.TextField("Доступное содержание / заметки", blank=True, max_length=20000)
    published_at = models.DateTimeField("Дата публикации", null=True, blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    state = models.CharField("Статус", max_length=12, choices=State.choices, default=State.NEW)

    class Meta:
        ordering = ["-discovered_at", "-pk"]

    def __str__(self):
        return self.title


class Draft(models.Model):
    class Channel(models.TextChoices):
        SOCIAL = "social", "Пост для соцсети"
        MESSENGER = "messenger", "Сообщение для мессенджера"
        EMAIL = "email", "Email-рассылка"

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        APPROVED = "approved", "Утверждён"

    article = models.ForeignKey(Article, on_delete=models.PROTECT, related_name="drafts")
    channel = models.CharField("Формат", max_length=12, choices=Channel.choices)
    subject = models.CharField("Тема email / рабочее название", max_length=200, blank=True)
    body = models.TextField("Текст материала", max_length=20000)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]


class DraftRevision(models.Model):
    draft = models.ForeignKey(Draft, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=12, choices=Draft.Status.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["draft", "version"], name="unique_draft_version")]
