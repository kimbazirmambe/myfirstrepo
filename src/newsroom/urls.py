from django.urls import path
from . import views

app_name = "newsroom"
urlpatterns = [
    path("articles/", views.article_list, name="articles"),
    path("articles/add/", views.article_add, name="article-add"),
    path("articles/import/", views.article_import, name="import"),
    path("articles/<int:pk>/", views.article_detail, name="article"),
    path("articles/<int:pk>/compose/", views.draft_create, name="compose"),
    path("publications/", views.draft_list, name="drafts"),
    path("publications/<int:pk>/", views.draft_detail, name="draft"),
    path("publications/<int:pk>/approve/", views.draft_approve, name="approve"),
    path("publications/<int:pk>/export/", views.draft_export, name="export"),
    path("history/", views.history, name="history"),
    path("settings/", views.settings_page, name="settings"),
]
