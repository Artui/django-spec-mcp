"""``NotificationKind`` - the notification types a client can opt in to, other than by URI or id."""

from __future__ import annotations

from enum import Enum


class NotificationKind(str, Enum):
    """The notification types a client can opt in to, other than by URI or id.

    **Opt-in is a MUST**, not a courtesy: *"the server MUST NOT send
    notification types the client has not explicitly requested."* So this is a
    closed set, and anything outside the request's filter is not sent.

    The values are the notification methods with the ``notifications/`` prefix
    stripped, and the filter field names are their camelCase forms — kept
    mechanically related so a new kind cannot be added to one and forgotten in
    the other.
    """

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
