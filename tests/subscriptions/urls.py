"""The notification-pushing mount the suites name by this module path.

Its drf-services server is kept in ``urls_drfs.py`` until the drfs path is
deleted, and re-exported here so the suites that still name this module mount
it: ``test_subscriptions_endtoend_drfs.py``, ``test_task_notifications_drfs.py``
and ``tests/handlers/test_capability_honesty.py``. The kernel suites mount
``kernel_urls.py`` instead. The deletion drops these lines with that file.
"""

from __future__ import annotations

from tests.subscriptions.urls_drfs import BROKER, SERVER, urlpatterns

__all__ = ["BROKER", "SERVER", "urlpatterns"]
