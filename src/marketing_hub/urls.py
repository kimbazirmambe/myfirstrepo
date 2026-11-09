from django.urls import include, path
from dashboard.views import home

urlpatterns = [
    path("", home, name="home"),
    path("sources/", include("sources.urls")),
]
