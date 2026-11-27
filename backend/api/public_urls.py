"""Routes of the public partner API, version 1 (DEV-393): /api/public/v1/. See api/public.py."""
from django.urls import path

from . import public

urlpatterns = [
    path("ping", public.ping),
    path("services", public.services),
    path("openapi.json", public.openapi),
]
