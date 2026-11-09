from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from .forms import ApprovalForm, ArticleForm, DraftForm, ImportForm
from .models import Article, Draft, DraftRevision
from .services import VersionConflict, import_articles, snapshot, update_draft


def page(request, queryset):
    return Paginator(queryset, 20).get_page(request.GET.get("page"))


@staff_member_required
def article_list(request):
    articles = Article.objects.select_related("source")
    query = request.GET.get("q", "").strip()[:200]
    state = request.GET.get("state", "")
    if query:
        articles = articles.filter(Q(title__icontains=query) | Q(body__icontains=query))
    if state in Article.State.values:
        articles = articles.filter(state=state)
    return render(request, "newsroom/articles.html", {
        "page": page(request, articles), "query": query, "state": state, "states": Article.State.choices,
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
        "note": "Напишите собственный текст на основе проверенных фактов. ИИ-провайдер пока не подключён: этот редактор не выполняет автоматическую генерацию.",
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
    return render(request, "newsroom/settings.html")
