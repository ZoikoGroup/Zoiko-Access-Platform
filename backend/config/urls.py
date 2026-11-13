from django.urls import include, path, re_path

from foundation.error_views import unsupported_version

urlpatterns = [
    path("", include("foundation.urls")),
    path("api/v1/", include("api.urls")),
]

# Any other /api/<version>/ answers safely and says which versions exist (GHE-13 T-05).
urlpatterns.append(re_path(
    r"^api/(?P<version>[A-Za-z0-9._-]{1,16})/(?P<rest>.*)$", unsupported_version))

# The hand-operated dev console. Every view refuses (404) unless DEBUG and DEV_AUTH_STUB are on.
urlpatterns.append(path("", include("devconsole.urls")))

# Django's own error pages, in our error format (no HTML, no leaked details).
handler400 = "foundation.error_views.bad_request"
handler403 = "foundation.error_views.permission_denied"
handler404 = "foundation.error_views.not_found"
handler500 = "foundation.error_views.server_error"
