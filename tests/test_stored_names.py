"""Every name this package writes somewhere that outlives a process.

A cache key, a Redis channel, a signing salt and a settings key are read back
by a later deploy, so each is a contract with the data already stored. These
are the package's own names, none carried from the package it continues: a
store keyed on an old prefix would keep that name for as long as the data
lives, and a salt shared with it would accept the other package's signed
state. Each value is pinned here so a rename cannot happen by accident.
"""

from __future__ import annotations

from django.test import override_settings

from django_spec_mcp._compat import tracing
from django_spec_mcp.auth.rate_limits import (
    fixed_window_rate_limit,
    sliding_window_rate_limit,
    token_bucket_rate_limit,
)
from django_spec_mcp.conf import DEFAULTS, get_setting
from django_spec_mcp.subscriptions import redis_subscription_broker
from django_spec_mcp.tasks import django_cache_task_store
from django_spec_mcp.transport import (
    django_cache_session_store,
    redis_sse_broker,
    redis_sse_replay_buffer,
)
from django_spec_mcp.types.request_state_salt import REQUEST_STATE_SALT


def test_every_stored_prefix_is_this_packages() -> None:
    assert {
        "session": django_cache_session_store._KEY_PREFIX,
        "task": django_cache_task_store._KEY_PREFIX,
        "sse": redis_sse_broker._DEFAULT_CHANNEL_PREFIX,
        "sse-replay": redis_sse_replay_buffer._DEFAULT_KEY_PREFIX,
        "sub": redis_subscription_broker._DEFAULT_CHANNEL_PREFIX,
        "rl": fixed_window_rate_limit._DEFAULT_KEY_PREFIX,
        "rl-sw": sliding_window_rate_limit._DEFAULT_KEY_PREFIX,
        "rl-tb": token_bucket_rate_limit._DEFAULT_KEY_PREFIX,
    } == {
        "session": "spec-mcp:session:",
        "task": "spec-mcp:task:",
        "sse": "spec-mcp:sse",
        "sse-replay": "spec-mcp:sse-replay",
        "sub": "spec-mcp:sub",
        "rl": "spec-mcp:rl",
        "rl-sw": "spec-mcp:rl-sw",
        "rl-tb": "spec-mcp:rl-tb",
    }


def test_the_signing_salt_is_this_packages() -> None:
    assert REQUEST_STATE_SALT == "django_spec_mcp.elicitation.request_state"


def test_the_tracer_is_named_for_the_package() -> None:
    assert tracing._TRACER_NAME == "django_spec_mcp"


def test_the_settings_key_is_spec_mcp() -> None:
    with override_settings(SPEC_MCP={"SERVER_INFO": {"name": "from-the-key"}}):
        assert get_setting("SERVER_INFO") == {"name": "from-the-key"}


def test_server_info_defaults_to_the_distribution_name() -> None:
    # The suite's settings name a server of their own, so read the default.
    assert DEFAULTS["SERVER_INFO"] == {"name": "django-spec-mcp"}
