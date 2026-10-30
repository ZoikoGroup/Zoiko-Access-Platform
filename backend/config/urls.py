from django.urls import include, path

urlpatterns = [
    path("", include("foundation.urls")),
    path("api/v1/", include("api.urls")),
]

# The hand-operated dev console. Every view refuses (404) unless DEBUG and DEV_AUTH_STUB are on.
urlpatterns.append(path("", include("devconsole.urls")))

# Django's own error pages, in our error format (no HTML, no leaked details).
handler400 = "foundation.error_views.bad_request"
handler403 = "foundation.error_views.permission_denied"
handler404 = "foundation.error_views.not_found"
handler500 = "foundation.error_views.server_error"
