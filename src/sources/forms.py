from django import forms

from .models import NewsSource


class NewsSourceForm(forms.ModelForm):
    class Meta:
        model = NewsSource
        fields = ["name", "rss_url", "is_active", "keywords", "exclude_keywords"]
        labels = {"rss_url": "Адрес RSS-ленты или страницы"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название источника"}),
            "rss_url": forms.URLInput(attrs={"placeholder": "https://example.com/feed.xml"}),
        }
        help_texts = {
            "keywords": "Например: банкротства. При добавлении или изменении слов запускается поиск по публичному sitemap сайта с учётом форм слов. Варианты через запятую. Результаты — в разделе «Найденные статьи».",
            "rss_url": "Укажите адрес RSS-ленты или страницы с http:// или https://. Можно указать сайт или RSS. Подключённый сборщик автоматически найдёт новые материалы по расписанию.",
        }
