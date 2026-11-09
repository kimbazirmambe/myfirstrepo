from django.urls import path

from . import views

app_name = "sources"
urlpatterns = [
    path("", views.source_list, name="list"),
    path("<int:pk>/delete/", views.source_delete, name="delete"),
]
