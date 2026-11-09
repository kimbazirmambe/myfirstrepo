from django import forms

from .models import NewsSource


class NewsSourceForm(forms.ModelForm):
    class Meta:
        model = NewsSource
        fields = ["name", "rss_url", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Название источника"}),
            "rss_url": forms.URLInput(attrs={"placeholder": "https://example.com/feed.xml"}),
        }
        help_texts = {
            "rss_url": "Укажите адрес RSS-ленты с http:// или https://. Содержимое ленты пока не проверяется и не скачивается.",
        }
