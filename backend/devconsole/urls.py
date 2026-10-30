from django.urls import path

from . import views

urlpatterns = [
    path("", views.page),
    path("dev/api/state", views.state),
    path("dev/api/<str:action>", views.act),
]
