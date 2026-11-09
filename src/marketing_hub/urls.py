from django.contrib import admin
from django.urls import include, path
from dashboard.views import home
from newsroom import collector_api

urlpatterns = [
    path("", home, name="home"),
    path("collector/queue/", collector_api.queue),
    path("collector/results/", collector_api.receive),
    path("admin/", admin.site.urls),
    path("sources/", include("sources.urls")),
    path("", include("newsroom.urls")),
]
