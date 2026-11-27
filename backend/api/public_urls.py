from django.urls import path

from . import public

urlpatterns = [
    path("openapi.json", public.openapi),
    path("ping", public.ping),
    path("services", public.services),
    path("webhooks", public.webhooks),
    path("webhooks/<str:webhook_id>/pause", public.webhook_pause),
    path("webhooks/<str:webhook_id>/resume", public.webhook_resume),
    path("webhooks/<str:webhook_id>/test", public.webhook_test),
    path("webhooks/<str:webhook_id>/deliveries", public.webhook_deliveries),
    path("deliveries/<str:delivery_id>/redrive", public.delivery_redrive),
]
