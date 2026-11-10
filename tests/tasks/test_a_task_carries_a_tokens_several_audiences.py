from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import pytest
from django.core.cache import cache

from django_spec_mcp.auth.audience import audience_matches
from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.build_worker_token import build_worker_token
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.types.task_status import TaskStatus

_HERE = "https://example.test/mcp/"
_OTHER = "https://example.test/other/"


def _record(audience: str | Sequence[str] | None) -> TaskRecord:
    created = "2026-07-31T10:00:00Z"
    return TaskRecord(
        task=Task(
            task_id="t1",
            status=TaskStatus.WORKING,
            created_at=created,
            last_updated_at=created,
            ttl_ms=60_000,
        ),
        tool_name="things.do",
        arguments={},
        principal_id="user:7",
        audience=audience,
    )


def _through_json(store: DjangoCacheTaskStore) -> None:
    """Rewrite the stored entry as a JSON-serialising cache would hand it back."""
    key = "spec-mcp:task:t1"
    cache.set(key, json.loads(json.dumps(cache.get(key))))


def _round_trip(kind: str, record: TaskRecord) -> TaskRecord | None:
    if kind == "memory":
        memory = InMemoryTaskStore()
        memory.create(record)
        return memory.get(record.task_id)
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(record)
    if kind == "cache-json":
        _through_json(store)
    return store.get(record.task_id)


@pytest.mark.parametrize("kind", ["memory", "cache", "cache-json"])
@pytest.mark.parametrize("audience", [[_OTHER, _HERE], (_OTHER, _HERE)], ids=["list", "tuple"])
def test_several_audiences_survive_every_store_and_reach_the_worker(
    kind: str, audience: Sequence[str]
) -> None:
    stored = _round_trip(kind, _record(audience))

    assert stored is not None
    assert audience_matches(stored.audience, _HERE)
    assert not audience_matches(stored.audience, "https://example.test/admin/")
    assert audience_matches(build_worker_token(stored).audience, _HERE)


@pytest.mark.parametrize("kind", ["memory", "cache", "cache-json"])
def test_the_record_holds_a_tuple_so_no_store_changes_its_kind(kind: str) -> None:
    """Normalised once, where the record is built, so every store returns the same type."""
    stored = _round_trip(kind, _record([_OTHER, _HERE]))

    assert stored is not None
    assert stored.audience == (_OTHER, _HERE)


@pytest.mark.parametrize("audience", [_HERE, None], ids=["one", "none"])
def test_one_audience_or_none_is_kept_as_it_is(audience: Any) -> None:
    """A ``str`` is a sequence too, and must not become a tuple of characters."""
    assert _record(audience).audience == audience
