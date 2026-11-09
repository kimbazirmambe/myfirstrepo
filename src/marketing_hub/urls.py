from django.contrib import admin
from django.urls import include, path
from dashboard.views import home

urlpatterns = [
    path("", home, name="home"),
    path("admin/", admin.site.urls),
    path("sources/", include("sources.urls")),
    path("", include("newsroom.urls")),
]
