from __future__ import annotations

from enum import Enum


class NotificationKind(str, Enum):
    """The notification types a client can opt in to, other than by URI or id."""

    TOOLS_LIST_CHANGED = "tools/list_changed"
    PROMPTS_LIST_CHANGED = "prompts/list_changed"
    RESOURCES_LIST_CHANGED = "resources/list_changed"

    @property
    def method(self) -> str:
        return f"notifications/{self.value}"

    @property
    def filter_field(self) -> str:
        """The ``SubscriptionFilter`` key that opts in to this kind."""
        return _NOTIFICATION_FILTER_FIELDS[self]


_NOTIFICATION_FILTER_FIELDS: dict[NotificationKind, str] = {
    NotificationKind.TOOLS_LIST_CHANGED: "toolsListChanged",
    NotificationKind.PROMPTS_LIST_CHANGED: "promptsListChanged",
    NotificationKind.RESOURCES_LIST_CHANGED: "resourcesListChanged",
}
