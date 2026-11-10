from __future__ import annotations

import json

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from django_spec_mcp.transport.redis_sse_replay_buffer import RedisSSEReplayBuffer


def _client() -> FakeAsyncRedis:
    """Fresh ``fakeredis`` client per test."""
    return FakeAsyncRedis(server=FakeServer())


async def _drain(it) -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    async for pair in it:
        out.append(pair)
    return out


async def test_every_write_asks_redis_to_trim_approximately_at_max_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bound is Redis's to keep, so what the buffer owns is the request."""
    client = _client()
    execute_command = client.execute_command
    sent: list[list[str]] = []

    async def spy(*args: object, **options: object) -> object:
        # Every redis-py command funnels through ``execute_command``, the
        if args[0] == "XADD":
            sent.append([a.decode() if isinstance(a, bytes) else str(a) for a in args])
        return await execute_command(*args, **options)

    monkeypatch.setattr(client, "execute_command", spy)
    buf = RedisSSEReplayBuffer(client, max_events=7)
    await buf.record("s", {"n": 1})
    await buf.record("s", {"n": 2})
    key = "spec-mcp:sse-replay:s"
    assert sent == [
        ["XADD", key, "MAXLEN", "~", "7", "*", "data", '{"n": 1}'],
        ["XADD", key, "MAXLEN", "~", "7", "*", "data", '{"n": 2}'],
    ]
    await client.aclose()


async def test_trimming_keeps_the_newest_max_events_and_bounds_the_rest() -> None:
    """``MAXLEN ~ N`` trims only whole internal nodes of the stream."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, max_events=2)
    key = "spec-mcp:sse-replay:s"
    recorded: list[tuple[str, object]] = []
    for n in range(1000):
        recorded.append((await buf.record("s", {"n": n}), {"n": n}))
        if await client.xlen(key) < len(recorded):
            break
    else:
        pytest.fail("1000 writes against max_events=2 and the stream was never trimmed")
    trimmed_at = len(recorded)

    out = await _drain(buf.replay("s", "0-0"))
    assert len(out) >= 2
    assert out == recorded[-len(out) :]

    for n in range(trimmed_at):
        await buf.record("s", {"n": n})
        assert await client.xlen(key) < trimmed_at
    await client.aclose()


@pytest.mark.parametrize("pad", [1000, 2100], ids=["1kb-events", "2kb-events"])
async def test_the_margin_over_max_events_stays_under_a_node_of_bytes(pad: int) -> None:
    """A node also closes at ``stream-node-max-bytes``, 4096 by default."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, max_events=2)
    key = "spec-mcp:sse-replay:s"
    event = {"pad": "x" * pad}
    event_bytes = len(json.dumps(event))
    for _ in range(300):
        await buf.record("s", event)
        margin = await client.xlen(key) - 2
        assert (margin + 1) * event_bytes < 4096
    await client.aclose()


def test_invalid_max_events_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        RedisSSEReplayBuffer(client=_client(), max_events=0)


async def test_import_error_when_redis_absent(monkeypatch) -> None:
    """The constructor surfaces a clear error when ``redis`` isn't installed."""
    import django_spec_mcp.transport.redis_sse_replay_buffer as mod

    monkeypatch.setattr(mod, "AsyncRedis", None)
    with pytest.raises(ImportError, match="django-spec-mcp\\[redis\\]"):
        RedisSSEReplayBuffer(client=object())


async def test_a_recorded_stream_carries_an_expiry() -> None:
    """``forget`` runs only on an explicit ``DELETE``."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=120)
    await buf.record("s", {"n": 1})
    ttl = await client.ttl("spec-mcp:sse-replay:s")
    assert 0 < ttl <= 120
    await client.aclose()


async def test_the_expiry_is_renewed_by_every_write() -> None:
    """An active stream must not expire under a client that is still there."""
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=120)
    await buf.record("s", {"n": 1})
    await client.expire("spec-mcp:sse-replay:s", 5)
    await buf.record("s", {"n": 2})
    assert await client.ttl("spec-mcp:sse-replay:s") > 5
    await client.aclose()


async def test_the_expiry_can_be_disabled() -> None:
    client = _client()
    buf = RedisSSEReplayBuffer(client, ttl_seconds=None)
    await buf.record("s", {"n": 1})
    # ``-1`` is Redis for "the key exists and never expires".
    assert await client.ttl("spec-mcp:sse-replay:s") == -1
    await client.aclose()


def test_an_unusable_expiry_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="ttl_seconds must be positive"):
        RedisSSEReplayBuffer(client=_client(), ttl_seconds=0)


async def test_two_servers_can_keep_separate_key_spaces() -> None:
    """The cache-backed stores fold the server's name into their key prefix."""
    client = _client()
    public = RedisSSEReplayBuffer(client, namespace="public")
    internal = RedisSSEReplayBuffer(client, namespace="internal")
    recorded = await public.record("shared-id", {"n": 1})
    assert await _drain(internal.replay("shared-id", "0-0")) == []
    assert await _drain(public.replay("shared-id", "0-0")) == [(recorded, {"n": 1})]
    await client.aclose()
