import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
application = get_asgi_application()

# Refuse to serve with development-only settings or a database login that could skip row-level
# security. With DEBUG on (development) nothing is refused.
from foundation.dbrole import assert_safe_database_role  # noqa: E402
from foundation.release_check import assert_release_ready  # noqa: E402

assert_release_ready()
assert_safe_database_role()
