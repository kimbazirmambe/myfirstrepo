from django import forms

from .models import NewsSource


class NewsSourceForm(forms.ModelForm):
    class Meta:
        model = NewsSource
        fields = ["name", "rss_url", "is_active"]
        labels = {"rss_url": "Адрес RSS-ленты или страницы"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название источника"}),
            "rss_url": forms.URLInput(attrs={"placeholder": "https://example.com/feed.xml"}),
        }
        help_texts = {
            "rss_url": "Укажите адрес RSS-ленты или страницы с http:// или https://. Для импорта файла адрес используется как источник; сетевых загрузок нет.",
        }
