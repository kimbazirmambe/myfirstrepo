from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import NewsSourceForm
from .models import NewsSource


@staff_member_required
@require_http_methods(["GET", "POST"])
def source_list(request):
    form = NewsSourceForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        source = form.save()
        queue_archive(source, request.user)
        if len(settings.COLLECTOR_TOKEN) >= 32:
            messages.success(request, "Источник добавлен и поставлен в очередь автоматической проверки.")
        else:
            messages.warning(request, "Источник сохранён. Автосборщик ещё не подключён — до подключения статьи автоматически не появятся.")
        return redirect("sources:list")
    return render(request, "sources/source_list.html", {
        "collector_configured": len(settings.COLLECTOR_TOKEN) >= 32,
        "form": form,
        "sources": NewsSource.objects.annotate(article_count=Count("article")),
    })


@staff_member_required
@require_http_methods(["GET", "POST"])
def source_delete(request, pk):
    source = get_object_or_404(NewsSource, pk=pk)
    if request.method == "POST":
        source.delete()
        messages.success(request, "Источник удалён.")
        return redirect("sources:list")
    return render(request, "sources/source_confirm_delete.html", {"source": source})


@staff_member_required
@require_http_methods(["GET", "POST"])
def source_edit(request, pk):
    source = get_object_or_404(NewsSource, pk=pk)
    previous_active = source.is_active
    previous_rules = (source.rss_url, source.keywords, source.exclude_keywords)
    form = NewsSourceForm(request.POST if request.method == "POST" else None, instance=source)
    if request.method == "POST" and form.is_valid():
        source = form.save(commit=False)
        if (source.rss_url, source.keywords, source.exclude_keywords) != previous_rules:
            source.seen_urls = []
            source.last_filtered_count = 0
        source.collection_requested = True
        source.collection_error = ""
        source.save()
        changed = (source.rss_url, source.keywords, source.exclude_keywords) != previous_rules
        if changed or (source.is_active and not previous_active):
            queue_archive(source, request.user, replace=changed)
        messages.success(request, "Источник обновлён; запрос на проверку сохранён.")
        return redirect("sources:list")
    return render(request, "sources/source_edit.html", {"form": form})


@staff_member_required
@require_http_methods(["POST"])
def source_retry(request, pk):
    source = get_object_or_404(NewsSource, pk=pk)
    if source.is_active:
        source.collection_requested = True
        source.collection_error = ""
        source.save(update_fields=["collection_requested", "collection_error"])
        messages.success(request, "Запрос на проверку сохранён. Результат появится после ближайшего запуска подключённого сборщика.")
    return redirect("sources:list")


def queue_archive(source, user, replace=True):
    from collectors.archive import terms
    from newsroom.models import ArchiveSearch
    if source.is_active and terms(source.keywords):
        if not replace and ArchiveSearch.objects.filter(source=source, status__in=['queued', 'running']).exists():
            return
        ArchiveSearch.objects.filter(source=source, status__in=['queued', 'running']).update(status='cancelled')
        ArchiveSearch.objects.create(source=source, source_url=source.rss_url, query=source.keywords, created_by=user)
