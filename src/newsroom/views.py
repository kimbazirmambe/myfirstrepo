from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ApprovalForm, ArticleForm, DraftForm, ImportForm
from .models import Article, Draft, DraftRevision, ArchiveSearch
from .services import VersionConflict, import_articles, snapshot, update_draft


def page(request, queryset):
    return Paginator(queryset, 20).get_page(request.GET.get("page"))


@staff_member_required
def article_list(request):
    articles = Article.objects.select_related("source")
    source_id = request.GET.get("source", "")
    if source_id.isdigit():
        articles = articles.filter(source_id=source_id)
    query = request.GET.get("q", "").strip()[:200]
    state = request.GET.get("state", "")
    if query:
        articles = articles.filter(Q(title__icontains=query) | Q(body__icontains=query) | Q(content_text__icontains=query))
    if state in Article.State.values:
        articles = articles.filter(state=state)
    return render(request, "newsroom/articles.html", {
        "page": page(request, articles), "source_id": source_id, "query": query, "state": state, "states": Article.State.choices, "searches": ArchiveSearch.objects.all()[:10],
    })


@staff_member_required
@require_http_methods(["GET", "POST"])
def article_add(request):
    form = ArticleForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        article = form.save()
        messages.success(request, "Статья сохранена.")
        return redirect("newsroom:article", pk=article.pk)
    return render(request, "newsroom/form.html", {"form": form, "title": "Добавить статью вручную", "button": "Сохранить статью"})


@staff_member_required
@require_http_methods(["GET", "POST"])
def article_import(request):
    form = ImportForm(request.POST if request.method == "POST" else None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            counts = import_articles(data["source"], data["file"].read(1024 * 1024 + 1), data["format"], data["keywords"], data["exclude"])
        except ValueError as error:
            form.add_error("file", str(error))
        else:
            messages.success(request, f"Добавлено: {counts['added']}; повторов: {counts['duplicates']}; отфильтровано: {counts['filtered']}; некорректных записей: {counts['invalid']}.")
            return redirect("newsroom:articles")
    return render(request, "newsroom/form.html", {
        "form": form, "title": "Импорт статей из файла", "button": "Импортировать",
        "note": "Загрузите разрешённо полученный RSS/Atom XML или HTML podelu.by в UTF-8 (до 1 МБ, до 100 записей). Файл разбирается локально на сервере; запросов к сайту нет. HTML-анализатор возвращает кандидатов: проверьте их вручную.",
    })


@staff_member_required
@require_http_methods(["GET", "POST"])
def article_detail(request, pk):
    article = get_object_or_404(Article.objects.select_related("source"), pk=pk)
    if request.method == "POST":
        state = request.POST.get("state")
        if state in Article.State.values:
            article.state = state
            article.save(update_fields=["state"])
            messages.success(request, "Статус статьи обновлён.")
            return redirect("newsroom:article", pk=article.pk)
        messages.error(request, "Выберите допустимый статус статьи.")
    return render(request, "newsroom/article.html", {"article": article, "states": Article.State.choices})


@staff_member_required
def draft_list(request):
    drafts = Draft.objects.select_related("article")
    return render(request, "newsroom/drafts.html", {"page": page(request, drafts)})


@staff_member_required
@require_http_methods(["GET", "POST"])
def draft_create(request, pk):
    article = get_object_or_404(Article, pk=pk)
    form = DraftForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            draft = form.save(commit=False)
            draft.article = article
            draft.save()
            snapshot(draft, request.user)
        messages.success(request, "Черновик сохранён. Перед утверждением проверьте факты и источник.")
        return redirect("newsroom:draft", pk=draft.pk)
    return render(request, "newsroom/form.html", {
        "form": form, "title": "Новый материал", "button": "Сохранить черновик", "article": article,
        "note": "Здесь можно написать собственный текст на основе проверенных фактов. Автоматические материалы создаёт подключённый сборщик; эта форма предназначена для ручной работы.",
    })


@staff_member_required
@require_http_methods(["GET", "POST"])
def draft_detail(request, pk):
    draft = get_object_or_404(Draft.objects.select_related("article", "approved_by"), pk=pk)
    form = DraftForm(request.POST if request.method == "POST" else None, instance=draft, initial={"version": draft.version})
    conflict = False
    if request.method == "POST" and form.is_valid():
        if form.cleaned_data.get("version") is None:
            form.add_error(None, "Обновите страницу: отсутствует номер версии.")
        else:
            try:
                update_draft(draft, form.cleaned_data["version"], request.user, changes={
                    key: form.cleaned_data[key] for key in ("channel", "subject", "body")
                })
            except VersionConflict as error:
                form.add_error(None, str(error))
                conflict = True
            else:
                messages.success(request, "Новая версия сохранена. Требуется повторное утверждение.")
                return redirect("newsroom:draft", pk=draft.pk)
    draft.refresh_from_db()
    return render(request, "newsroom/draft.html", {
        "draft": draft, "form": form,
        "approval": ApprovalForm(initial={"version": draft.version}),
        "revisions": draft.revisions.select_related("actor"),
    }, status=409 if conflict else 200)


@staff_member_required
@require_POST
def draft_approve(request, pk):
    draft = get_object_or_404(Draft, pk=pk)
    form = ApprovalForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Подтвердите проверку фактов и источника перед утверждением.")
    else:
        try:
            update_draft(draft, form.cleaned_data["version"], request.user, approve=True)
        except VersionConflict as error:
            messages.error(request, str(error))
        else:
            messages.success(request, "Версия утверждена. Отправка в каналы не выполнялась.")
    return redirect("newsroom:draft", pk=draft.pk)


@staff_member_required
def draft_export(request, pk):
    draft = get_object_or_404(Draft.objects.select_related("article"), pk=pk)
    text = f"{draft.get_status_display()} · версия {draft.version}\n{draft.get_channel_display()}\n\n"
    if draft.subject:
        text += draft.subject + "\n\n"
    text += draft.body + "\n\nИсточник: " + draft.article.url + "\n"
    response = HttpResponse(text, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="material-{draft.pk}-v{draft.version}.txt"'
    return response


@staff_member_required
def history(request):
    revisions = DraftRevision.objects.select_related("draft__article", "actor")
    return render(request, "newsroom/history.html", {"page": page(request, revisions)})


@staff_member_required
def settings_page(request):
    return render(request, "newsroom/settings.html", {"collector_configured": len(settings.COLLECTOR_TOKEN) >= 32})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def archive_start(request):
    from .forms import ArchiveSearchForm
    from .models import ArchiveSearch
    form = ArchiveSearchForm(request.POST if request.method == 'POST' else None, initial={'source': request.GET.get('source')})
    if request.method == 'POST' and form.is_valid():
        source = form.cleaned_data['source']
        job = ArchiveSearch.objects.create(source=source, source_url=source.rss_url,
                                          query=form.cleaned_data['query'], created_by=request.user)
        return redirect('newsroom:archive', pk=job.pk)
    return render(request, 'newsroom/form.html', {'form': form, 'title': 'Найти статьи на сайте',
        'button': 'Начать поиск', 'note': 'Поиск проверяет общедоступные статьи из sitemap сайта. Запускается внешним сборщиком примерно раз в 15 минут; большой архив потребует нескольких запусков. Закрытые страницы не читаются. Посты создаются по вашей команде.'})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def archive_detail(request, pk):
    from .models import ArchiveSearch
    job = get_object_or_404(ArchiveSearch, pk=pk)
    if request.method == 'POST':
        ArchiveSearch.objects.filter(pk=pk, status__in=['queued', 'running']).update(status='cancelled')
        return redirect('newsroom:archive', pk=pk)
    return render(request, 'newsroom/archive.html', {'job': job, 'page': page(request, job.articles.all())})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def generate_post(request, pk):
    from django.db import IntegrityError
    from .forms import GeneratePostForm
    from .models import GenerationRequest
    article = get_object_or_404(Article, pk=pk)
    form = GeneratePostForm(request.POST if request.method == 'POST' else None)
    if request.method == 'POST' and form.is_valid():
        try:
            with transaction.atomic():
                job = GenerationRequest.objects.create(article=article, channel=form.cleaned_data['channel'],
                    editorial_name=form.cleaned_data['editorial_name'], created_by=request.user)
        except IntegrityError:
            job = GenerationRequest.objects.get(article=article, channel=form.cleaned_data['channel'], status__in=['queued', 'running'])
        return redirect('newsroom:generation', pk=job.pk)
    return render(request, 'newsroom/form.html', {'form': form, 'article': article, 'title': 'Сгенерировать пост',
        'button': 'Заказать ИИ-черновик', 'note': 'Создаётся один самостоятельный материал выбранного формата с указанием источника. Нужен подключённый ключ Gemini из проекта без платного биллинга. При исчерпании бесплатной квоты запрос завершится сообщением об ошибке; платного переключения нет. Перед публикацией проверьте факты и права. Выполнение — при ближайшем запуске сборщика.'})


@staff_member_required
def generation_detail(request, pk):
    from .models import GenerationRequest
    job = get_object_or_404(GenerationRequest.objects.select_related('article', 'draft'), pk=pk)
    return render(request, 'newsroom/generation.html', {'job': job})
