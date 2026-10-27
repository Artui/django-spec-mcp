"""The task-running mount the end-to-end suite names by this module path.

Its drf-services server is kept in ``urls_drfs.py`` until the drfs path is
deleted, and re-exported here so ``test_tasks_endtoend_drfs.py`` still mounts it
under the name it was written against. Every tool on it carries a
``task_policy``, which a kernel spec does not take yet, so there is no kernel
twin of this mount. The deletion drops these lines with that file.
"""

from __future__ import annotations

from tests.tasks.urls_drfs import EXECUTOR, SERVER, STORE, urlpatterns

__all__ = ["EXECUTOR", "SERVER", "STORE", "urlpatterns"]
