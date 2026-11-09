from django.shortcuts import render


def home(request):
    stats = None
    if request.user.is_authenticated and request.user.is_active and request.user.is_staff:
        from sources.models import NewsSource
        from newsroom.models import Article, Draft
        stats = {
            "sources": NewsSource.objects.count(),
            "articles": Article.objects.count(),
            "drafts": Draft.objects.filter(status=Draft.Status.DRAFT).count(),
            "approved": Draft.objects.filter(status=Draft.Status.APPROVED).count(),
        }
    return render(request, "dashboard/home.html", {"stats": stats})
