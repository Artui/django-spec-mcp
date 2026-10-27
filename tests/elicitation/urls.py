"""The mount whose one tool needs a confirmation, named by the HTTP suite.

Its drf-services server is kept in ``urls_drfs.py`` until the drfs path is
deleted, and re-exported here so ``test_elicitation_endtoend.py`` still mounts
it under the name it was written against. The deletion drops these lines with
that file.
"""

from __future__ import annotations

from tests.elicitation.urls_drfs import SERVER, urlpatterns

__all__ = ["SERVER", "urlpatterns"]
