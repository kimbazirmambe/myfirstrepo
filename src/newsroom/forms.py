from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from django import forms
from django.core.validators import URLValidator

from sources.models import NewsSource
from .models import Article, Draft


def normalize_url(value):
    URLValidator(schemes=["http", "https"])(value)
    parts = urlsplit(value)
    if parts.username is not None or parts.password is not None:
        raise forms.ValidationError("Адрес не должен содержать имя пользователя или пароль.")
    query = [(key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["source", "title", "url", "body", "published_at"]
        widgets = {"body": forms.Textarea(attrs={"rows": 6}),
                   "published_at": forms.DateTimeInput(attrs={"type": "datetime-local"})}
        help_texts = {"body": "Сохраните разрешённый фрагмент или свои заметки. Не копируйте чужую статью целиком без разрешения.",
                      "published_at": "Необязательно. Если дата неизвестна, оставьте поле пустым. Время — UTC."}

    def clean_url(self):
        return normalize_url(self.cleaned_data["url"])


class ImportForm(forms.Form):
    source = forms.ModelChoiceField(label="Источник", queryset=NewsSource.objects.filter(is_active=True))
    format = forms.ChoiceField(label="Формат файла", choices=[("rss", "RSS / Atom"), ("html", "HTML podelu.by")])
    file = forms.FileField(label="Локальный файл")
    keywords = forms.CharField(label="Ключевые слова через запятую", required=False,
                               max_length=500, help_text="Хотя бы одно слово должно встретиться в заголовке или описании. Пусто — все материалы.")
    exclude = forms.CharField(label="Исключающие слова через запятую", required=False, max_length=500)

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        if uploaded.size > 1024 * 1024:
            raise forms.ValidationError("Максимальный размер файла — 1 МБ.")
        return uploaded


class DraftForm(forms.ModelForm):
    version = forms.IntegerField(widget=forms.HiddenInput, required=False)

    class Meta:
        model = Draft
        fields = ["channel", "subject", "body"]
        widgets = {"body": forms.Textarea(attrs={"rows": 12})}

    def clean(self):
        data = super().clean()
        if data.get("channel") == Draft.Channel.EMAIL and not data.get("subject"):
            self.add_error("subject", "Укажите тему email.")
        return data


class ApprovalForm(forms.Form):
    version = forms.IntegerField(widget=forms.HiddenInput)
    reviewed = forms.BooleanField(label="Я проверил факты, источник и права на использование текста")


class ArchiveSearchForm(forms.Form):
    source = forms.ModelChoiceField(label='Сайт / источник', queryset=NewsSource.objects.filter(is_active=True))
    query = forms.CharField(label='Что искать', max_length=500, help_text='Например: банкротства. Формы слов учитываются. Несколько слов — вместе; варианты через запятую — любой из них.')

    def clean_query(self):
        from collectors.archive import terms
        value = self.cleaned_data['query']
        if not terms(value):
            raise forms.ValidationError('Введите хотя бы одно слово для поиска.')
        return value


class GeneratePostForm(forms.Form):
    channel = forms.ChoiceField(label='Формат публикации', choices=Draft.Channel.choices)
    editorial_name = forms.CharField(label='Название вашей редакции', max_length=100, initial='Наша редакция')
    consent = forms.BooleanField(label='Разрешаю передать общедоступный текст статьи в Gemini Free Tier. Google может использовать эти данные для улучшения сервисов. Конфиденциальные данные не передаю.')
