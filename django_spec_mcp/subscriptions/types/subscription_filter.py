from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django_spec_mcp.types.notification_kind import NotificationKind


@dataclass(frozen=True)
class SubscriptionFilter:
    """What a subscription asked for, and what the server agreed to."""

    kinds: frozenset[NotificationKind] = frozenset()
    resource_uris: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()

    @classmethod
    def from_params(cls, raw: Any) -> SubscriptionFilter:
        """Read a filter off the wire, ignoring anything unusable."""
        if not isinstance(raw, dict):
            return cls()
        kinds: set[NotificationKind] = {
            kind for kind in NotificationKind if raw.get(kind.filter_field) is True
        }
        return cls(
            kinds=frozenset(kinds),
            resource_uris=_strings(raw.get("resourceSubscriptions")),
            task_ids=_strings(raw.get("taskIds")),
        )

    def to_dict(self) -> dict[str, Any]:
        """The wire form, omitting everything not asked for."""
        out: dict[str, Any] = {kind.filter_field: True for kind in sorted(self.kinds, key=str)}
        if self.resource_uris:
            out["resourceSubscriptions"] = list(self.resource_uris)
        if self.task_ids:
            out["taskIds"] = list(self.task_ids)
        return out

    def __bool__(self) -> bool:
        return bool(self.kinds or self.resource_uris or self.task_ids)


def _strings(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(item for item in raw if isinstance(item, str) and item))


__all__ = ["SubscriptionFilter"]
