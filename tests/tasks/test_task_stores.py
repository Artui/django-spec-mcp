from __future__ import annotations

import dataclasses
import time
import warnings
from typing import Any

import pytest
from django.core.cache import cache
from django.core.cache.backends.base import CacheKeyWarning

from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.tasks.django_cache_task_store import DjangoCacheTaskStore
from django_spec_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from django_spec_mcp.tasks.types.task_record import TaskRecord
from django_spec_mcp.tasks.utils import new_task_id
from django_spec_mcp.types.task_status import TaskStatus


def _record(task_id: str = "t1", **task_kwargs: Any) -> TaskRecord:
    defaults: dict[str, Any] = {
        "task_id": task_id,
        "status": TaskStatus.WORKING,
        "created_at": "2026-07-31T10:00:00Z",
        "last_updated_at": "2026-07-31T10:00:00Z",
        "ttl_ms": 60_000,
        "poll_interval_ms": 500,
    }
    defaults.update(task_kwargs)
    return TaskRecord(
        task=Task(**defaults),
        tool_name="things.do",
        arguments={"a": 1},
        principal_id="user:7",
        user_pk=7,
        scopes=("things:write",),
        audience="https://example.test/mcp",
    )


@pytest.fixture(params=["memory", "cache"])
def store(request: Any) -> Any:
    if request.param == "memory":
        return InMemoryTaskStore()
    cache.clear()
    return DjangoCacheTaskStore(namespace="s")


# ----- the contract both backends keep -----


def test_a_created_task_reads_back(store: Any) -> None:
    store.create(_record())
    assert store.get("t1") is not None


def test_everything_needed_to_replay_the_call_survives(store: Any) -> None:
    """The record is only worth storing if the worker can act on it."""
    store.create(_record())
    back: TaskRecord = store.get("t1")
    assert back.tool_name == "things.do"
    assert back.arguments == {"a": 1}
    assert back.principal_id == "user:7"
    assert back.user_pk == 7
    assert back.scopes == ("things:write",)
    assert back.audience == "https://example.test/mcp"


def test_an_unknown_id_is_none_not_an_error(store: Any) -> None:
    assert store.get("nope") is None


def test_save_overwrites(store: Any) -> None:
    store.create(_record())
    store.save(_record().with_task(status=TaskStatus.COMPLETED, result={"ok": 1}))
    back: TaskRecord = store.get("t1")
    assert back.status is TaskStatus.COMPLETED
    assert back.task.result == {"ok": 1}


def test_delete_removes(store: Any) -> None:
    store.create(_record())
    store.delete("t1")
    assert store.get("t1") is None


def test_deleting_an_unknown_id_is_not_an_error(store: Any) -> None:
    store.delete("never-existed")


def test_two_tasks_do_not_collide(store: Any) -> None:
    store.create(_record("a"))
    store.create(_record("b"))
    assert store.get("a").task_id == "a"
    assert store.get("b").task_id == "b"


# ----- cache-backed specifics -----


def test_the_cache_store_holds_a_plain_dict_not_a_pickled_dataclass() -> None:
    """A pickled dataclass would be a copy of *this version's* class."""
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    raw: Any = _envelope("t1")
    assert isinstance(raw, dict)
    assert raw["status"] == "working"


def test_save_renews_the_remaining_lifetime_not_the_whole_ttl() -> None:
    """The bug this prevents: a task reporting often would never expire."""
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record(ttl_ms=60_000))
    first: Any = _envelope("t1")["expiresAt"]
    time.sleep(0.01)
    store.save(_record(ttl_ms=60_000).with_task(status_message="tick"))
    assert _envelope("t1")["expiresAt"] == first


def test_a_task_with_no_ttl_still_gets_a_backstop() -> None:
    """None means 'no expiry' on the wire; the cache still needs a bound."""
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record(ttl_ms=None))
    assert _envelope("t1")["expiresAt"] is None
    assert store.get("t1") is not None


def test_saving_a_task_the_cache_has_already_dropped_still_writes() -> None:
    """The expiry lookup misses, so the write falls back to the no-TTL bound rather than failing."""
    cache.clear()
    store = DjangoCacheTaskStore(namespace="s")
    store.save(_record())
    assert store.get("t1") is not None


def test_an_already_expired_absolute_deadline_does_not_write_a_zero_timeout() -> None:
    """A zero timeout means 'expire immediately' on several backends."""
    cache.clear()
    store = DjangoCacheTaskStore(namespace="s")
    store.create(_record(ttl_ms=1))
    time.sleep(0.01)
    store.save(_record().with_task(status=TaskStatus.COMPLETED))
    assert store.get("t1") is not None


@pytest.mark.parametrize(
    "entry",
    [
        pytest.param({"taskId": "t1", "status": "not-a-status"}, id="an-unknown-status"),
        pytest.param({"status": "working"}, id="a-missing-field"),
        pytest.param("clobbered", id="not-a-dict"),
    ],
)
def test_an_unreadable_entry_reads_as_a_task_that_does_not_exist(entry: Any) -> None:
    """Written by another version, or by something else sharing the cache."""
    cache.clear()
    store = DjangoCacheTaskStore()
    store.create(_record())
    cache.set(_key("t1"), entry)
    assert store.get("t1") is None


def test_two_servers_in_one_project_cannot_see_each_others_tasks() -> None:
    """The same namespacing the session store does, for the same reason."""
    cache.clear()
    a = DjangoCacheTaskStore(namespace="server-a")
    b = DjangoCacheTaskStore(namespace="server-b")
    a.create(_record())
    assert b.get("t1") is None
    assert a.get("t1") is not None


@pytest.mark.parametrize(
    "namespace",
    [
        # ``name`` is consumer-supplied prose; memcached rejects spaces and caps
        pytest.param("My Invoicing Server ✨", id="a-free-form-server-name"),
        pytest.param(None, id="no-namespace"),
    ],
)
def test_a_store_produces_a_usable_key_under_any_namespace(namespace: str | None) -> None:
    cache.clear()
    store = DjangoCacheTaskStore(namespace=namespace)
    store.create(_record())
    assert store.get("t1") is not None


# The un-namespaced key format, which the store documents: a namespace is
def _key(task_id: str) -> str:
    return f"spec-mcp:task:{task_id}"


def _envelope(task_id: str) -> Any:
    return cache.get(_key(task_id))


# --- codec exhaustiveness -------------------------------------------------


def _distinct_record() -> TaskRecord:
    """A record whose every field differs from its default."""
    return TaskRecord(
        task=Task(
            task_id="codec-1",
            status=TaskStatus.WORKING,
            created_at="2026-08-10T00:00:00Z",
            last_updated_at="2026-08-10T00:00:01Z",
            ttl_ms=1234,
            poll_interval_ms=567,
            status_message="halfway",
            result={"r": 1},
            error={"e": 2},
            input_requests={"i": 3},
        ),
        tool_name="tool",
        arguments={"a": 1},
        principal_id="user:7",
        user_pk=7,
        scopes=("read", "write"),
        audience="aud",
        enqueued=True,
        input_responses={"q": "a"},
        progress=42.0,
        total=100.0,
        can_elicit=True,
        input_round=3,
    )


_REQUIRED = object()
"""Stands in for "this field has no default", so nothing can equal it."""


def _default_of(f: Any) -> Any:
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return _REQUIRED


def test_every_field_of_the_fixture_is_non_default() -> None:
    """The fixture must exercise every field, or the round trip proves nothing."""
    record = _distinct_record()
    stale: list[str] = []
    for obj, label in ((record, "TaskRecord"), (record.task, "Task")):
        for f in dataclasses.fields(obj):
            if getattr(obj, f.name) == _default_of(f):
                stale.append(f"{label}.{f.name}")
    assert stale == [], f"fixture leaves these at their default: {stale}"


@pytest.mark.django_db
def test_the_cache_codec_preserves_every_field() -> None:
    """Save → load must return a record equal field by field."""
    store = DjangoCacheTaskStore(namespace="codec")
    original = _distinct_record()
    store.create(original)
    loaded = store.get(original.task.task_id)

    assert loaded is not None
    for f in dataclasses.fields(original):
        assert getattr(loaded, f.name) == getattr(original, f.name), f"lost TaskRecord.{f.name}"
    for f in dataclasses.fields(original.task):
        assert getattr(loaded.task, f.name) == getattr(original.task, f.name), f"lost Task.{f.name}"


# The keys ``DjangoCacheTaskStore`` wrote in 0.6.0, the last release before a
# task could ask for input, copied from that release's ``_serialize``: a cache
# holds a record for its TTL, so one written then is read by this release.
_A_0_6_0_RECORD: dict[str, Any] = {
    "taskId": "upgrade-1",
    "status": "working",
    "createdAt": "2026-11-03T00:00:00Z",
    "lastUpdatedAt": "2026-11-03T00:00:01Z",
    "ttlMs": 60000,
    "pollIntervalMs": 500,
    "statusMessage": None,
    "result": None,
    "error": None,
    "inputRequests": None,
    "toolName": "tool",
    "arguments": {"a": 1},
    "principalId": "anonymous",
    "userPk": None,
    "scopes": ["read"],
    "audience": None,
    "enqueued": True,
    "inputResponses": {},
    "progress": None,
    "total": None,
    "expiresAt": None,
}


def test_a_record_in_the_0_6_0_shape_still_loads() -> None:
    """A record written before can_elicit and input_round existed runs as it would have."""
    store = DjangoCacheTaskStore(namespace="upgrade")
    cache.set(store._key("upgrade-1"), dict(_A_0_6_0_RECORD))

    loaded = store.get("upgrade-1")

    assert loaded is not None
    assert loaded.arguments == {"a": 1}
    assert loaded.enqueued is True
    assert loaded.can_elicit is False
    assert loaded.input_round == 0


# ----- ids that could not be ours -----


@pytest.mark.parametrize(
    "task_id",
    [
        pytest.param("a b", id="space"),
        pytest.param("a\x7fb", id="control-character"),
        pytest.param("x" * 300, id="over-the-key-length-limit"),
    ],
)
def test_a_malformed_id_is_answered_as_a_miss_not_an_exception(task_id: str) -> None:
    """``taskId`` comes off the wire and lands in a cache key."""
    store = DjangoCacheTaskStore(namespace="ns")
    with warnings.catch_warnings():
        warnings.simplefilter("error", CacheKeyWarning)
        assert store.get(task_id) is None
        store.delete(task_id)


def test_a_wellformed_id_still_round_trips() -> None:
    """The guard has to admit every id this package actually issues."""
    store = DjangoCacheTaskStore(namespace="ns")
    record = _record(task_id=new_task_id())
    store.create(record)
    with warnings.catch_warnings():
        warnings.simplefilter("error", CacheKeyWarning)
        assert store.get(record.task_id) is not None
        store.delete(record.task_id)
    assert store.get(record.task_id) is None
