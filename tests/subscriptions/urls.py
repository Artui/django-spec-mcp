"""The notification-pushing mount the end-to-end suites name by this module path.

Its drf-services server is kept in ``urls_drfs.py`` until the drfs path is
deleted, and re-exported here so every ``pytest.mark.urls`` naming this module
(this directory's ``_drfs`` originals and ``test_capability_honesty``) still
mounts it. The deletion drops these lines with that file.
"""

from __future__ import annotations

from tests.subscriptions.urls_drfs import BROKER, SERVER, urlpatterns

__all__ = ["BROKER", "SERVER", "urlpatterns"]
