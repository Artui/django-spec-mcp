"""``TaskPolicy`` - whether a binding may — or must — answer with a task handle."""

from __future__ import annotations

from enum import Enum


class TaskPolicy(str, Enum):
    """Whether a binding may — or must — answer with a task handle.

    **A policy surface, not a wire field.** The shipped extension makes *"the
    server the sole decider; clients do not signal task preference on the
    request itself"*, so the decision has to be made on this side, and the
    binding is where every other per-tool knob lives.

    Attributes:
        FORBIDDEN: Never a task. The default, so every tool registered before
            this existed behaves exactly as it did.
        OPTIONAL: A task for a client that declared the extension, an ordinary
            inline call for one that did not. The safe choice for a slow tool
            that can still run inline.
        REQUIRED: A task, or nothing — a client that did not declare the
            extension gets ``-32021``. For work that genuinely cannot finish
            inside a request, where running it anyway would just hit the
            deadline.
    """

    FORBIDDEN = "forbidden"
    OPTIONAL = "optional"
    REQUIRED = "required"
