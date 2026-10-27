"""``SUBSCRIPTIONS_ACKNOWLEDGED_METHOD`` - the first notification on every subscription, naming the filter the server agreed to honour."""

from __future__ import annotations

SUBSCRIPTIONS_ACKNOWLEDGED_METHOD: str = "notifications/subscriptions/acknowledged"
"""**MUST be the first message a subscription carries.** It reports the subset
of the requested filter the server agreed to honour, so a client learns
immediately that (say) ``promptsListChanged`` will never arrive because this
server has no prompts, rather than waiting indefinitely for it."""
