"""The task wire type, the policy branches, and the two task config scalars.

The remaining corners: the status-specific field ``Task.to_dict`` emits, the
``OPTIONAL`` fallbacks that quietly run a tool inline, the ``REQUIRED`` denial,
the async path's task branch, and settings whose ``None`` is meaningful.

Only the questions that name no tool are asked here: the wire type and the
two config scalars. Each policy branch registers a drf-services tool with a
``task_policy``: the ``OPTIONAL`` fallbacks to inline, the denial before a task
is created, a binding predating tasks, the async path's handle and the three
rate-limit charges. A kernel spec does not take one yet (``register_tool``
refuses it with ``ImproperlyConfigured``), so they are owed until it does. The original is kept beside this
file as ``test_task_wire_shape_drfs.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.test import override_settings

from django_spec_mcp.config.build_mcp_config import build_mcp_config
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.types.task_status import TaskStatus


def _task(**kwargs: Any) -> Task:
    defaults: dict[str, Any] = {
        "task_id": "t",
        "status": TaskStatus.WORKING,
        "created_at": "2026-07-31T10:00:00Z",
        "last_updated_at": "2026-07-31T10:00:00Z",
        "ttl_ms": None,
    }
    defaults.update(kwargs)
    return Task(**defaults)


# ----- Task.to_dict -----


def test_ttl_is_always_present_even_when_null() -> None:
    """``ttlMs: number | null`` in the schema — an omission and a ``null`` mean
    different things to a client deciding when to stop polling."""
    assert _task().to_dict()["ttlMs"] is None


def test_optional_fields_are_omitted_rather_than_nulled() -> None:
    emitted = _task().to_dict()
    assert "pollIntervalMs" not in emitted
    assert "statusMessage" not in emitted


def test_a_status_message_rides_along_when_set() -> None:
    assert _task(status_message="halfway").to_dict()["statusMessage"] == "halfway"


def test_the_status_decides_which_extra_field_appears_not_what_is_stored() -> None:
    """A record holding both a result and an error must not emit a shape no
    variant in the spec describes. The status is the single source of truth."""
    emitted = _task(
        status=TaskStatus.COMPLETED, result={"a": 1}, error={"b": 2}, input_requests={"c": 3}
    ).to_dict()
    assert emitted["result"] == {"a": 1}
    assert "error" not in emitted
    assert "inputRequests" not in emitted


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (TaskStatus.COMPLETED, "result"),
        (TaskStatus.FAILED, "error"),
        (TaskStatus.INPUT_REQUIRED, "inputRequests"),
    ],
)
def test_a_mandatory_field_is_an_empty_object_rather_than_missing(
    status: TaskStatus, key: str
) -> None:
    """The spec makes each mandatory for its status. A client that unwraps it
    unconditionally breaks on a missing key and survives an empty object."""
    assert _task(status=status).to_dict()[key] == {}


def test_a_cancelled_task_carries_no_extra_field() -> None:
    emitted = _task(status=TaskStatus.CANCELLED).to_dict()
    assert set(emitted) == {"taskId", "status", "createdAt", "lastUpdatedAt", "ttlMs"}


def test_terminal_statuses_are_exactly_the_three() -> None:
    assert [s for s in TaskStatus if s.is_terminal] == [
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    ]


# ----- the policy branches -----


# ----- the async path -----


# ----- config -----


def test_the_task_scalars_come_from_settings_by_default() -> None:
    config = build_mcp_config()
    assert config.task_ttl_ms == 86_400_000
    assert config.task_poll_interval_ms == 5_000


def test_an_explicit_value_wins_over_the_setting() -> None:
    assert build_mcp_config(task_ttl_ms=1_000).task_ttl_ms == 1_000


@override_settings(SPEC_MCP={"TASK_TTL_MS": None, "TASK_POLL_INTERVAL_MS": None})
def test_none_in_settings_means_none_rather_than_unset() -> None:
    """Why these two cannot use the ``x if x is not None else setting`` shape
    the other scalars use: ``None`` is a configured answer here — "never
    expire", "send no poll hint" — not an absent one."""
    config = build_mcp_config()
    assert config.task_ttl_ms is None
    assert config.task_poll_interval_ms is None


def test_an_explicit_none_disables_expiry_rather_than_reading_the_setting() -> None:
    """The other half of the same distinction, from the caller's side.

    ``build_mcp_config(task_ttl_ms=None)`` is a consumer asking for tasks that
    never expire. Reading ``None`` as "not supplied" answered it with the
    setting's 24 hours instead, and the only sign was records vanishing a day
    in. ``UNSET`` is what carries "omitted" for every other nullable bound
    here, and now for these two.
    """
    assert build_mcp_config(task_ttl_ms=None).task_ttl_ms is None
    assert build_mcp_config(task_poll_interval_ms=None).task_poll_interval_ms is None
    # Omitted still means the setting, which is what makes the two distinct.
    assert build_mcp_config().task_ttl_ms == 86_400_000
    assert build_mcp_config().task_poll_interval_ms == 5_000


# ----- rate limits -----


class _CountingLimiter:
    """Records each charge; refuses once ``deny_after`` charges have landed."""

    def __init__(self, *, deny_after: int | None = None) -> None:
        self.charges: int = 0
        self._deny_after = deny_after

    def consume(self, request: Any, token: Any) -> int | None:
        self.charges += 1
        if self._deny_after is not None and self.charges > self._deny_after:
            return 42
        return None
