from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .forms import NewsSourceForm
from .models import NewsSource


@staff_member_required
@require_http_methods(["GET", "POST"])
def source_list(request):
    form = NewsSourceForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Источник добавлен.")
        return redirect("sources:list")
    return render(request, "sources/source_list.html", {
        "form": form,
        "sources": NewsSource.objects.all(),
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
