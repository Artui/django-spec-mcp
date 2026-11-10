from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.types.task_status import TaskStatus


@dataclass(frozen=True)
class TaskRecord:
    """What a store holds: the wire task plus what a worker needs."""

    task: Task
    tool_name: str
    arguments: dict[str, Any]
    principal_id: str
    user_pk: Any = None
    scopes: tuple[str, ...] = field(default_factory=tuple)
    audience: str | Sequence[str] | None = None
    enqueued: bool = False

    progress: float | None = None
    """How far along the running task said it was, or ``None`` if it never said.

    Written by
    ``report_task_progress``,
    what a task's ``progress`` kwarg-pool seed resolves to. **Server-side only,
    by protocol**: the wire ``Task`` carries ``statusMessage`` and no numeric
    field, so a polling client sees only the *rendered* string this and
    ``total`` produce."""

    total: float | None = None
    """What ``progress`` counts toward, or ``None`` for an open-ended count.

    ``None`` is the ordinary case for work that cannot say how much there is —
    the reporter renders a bare count rather than inventing a denominator."""

    input_responses: dict[str, Any] = field(default_factory=dict)
    """Answers the client has supplied via ``tasks/update``, keyed as the
    matching ``inputRequests`` were.

    Accumulated rather than replaced, because the spec lets a client answer a
    strict subset of what is outstanding and come back for the rest; a worker
    parked on ``input_required`` reads this to find out what it was told.

    The spec requires the server never to reuse a key over a task's lifetime, so
    a key appearing here is answered for good and a later request picks a new
    one."""

    can_elicit: bool = False
    """Whether the request that created the task declared form elicitation.

    Capabilities are per request, and the worker serves no client, so this is the
    only record of whether the client can be asked. It gates parking: a worker whose
    service raises ``AdditionalInputRequired`` moves the task to ``input_required``
    only when this is true, and otherwise completes it with the ``isError`` result
    the inline call gives a client that cannot be asked. ``False`` is also what a
    record written before the field existed reads as, so such a task never parks
    (``test_a_record_in_the_0_6_0_shape_still_loads``)."""

    input_round: int = 0
    """How many questions the task has asked, and so the round the worker's next
    run is on.

    Kept on the record rather than in a signed ``requestState``: the inline
    exchange signs it because it travels through the client, while a task's lives
    here, and a signature would expire with ``INPUT_REQUEST_TTL_SECONDS`` though the
    task waits for its own TTL
    (``test_an_answer_long_after_the_question_still_completes_the_task``). It bounds
    the exchange by ``MAX_INPUT_ROUNDS``, as the inline round counter does, and
    names each question's key (``input_request_key``), which the spec forbids
    reusing over the task's lifetime."""

    def __post_init__(self) -> None:
        # A ``str`` is a sequence too, so it is excluded first.
        if self.audience is not None and not isinstance(self.audience, str):
            object.__setattr__(self, "audience", tuple(self.audience))

    @property
    def task_id(self) -> str:
        return self.task.task_id

    @property
    def status(self) -> TaskStatus:
        return self.task.status

    def to_wire(self) -> Task:
        return self.task

    def with_task(self, **changes: Any) -> TaskRecord:
        """Return a copy with fields changed on the embedded task."""
        return replace(self, task=replace(self.task, **changes))


__all__ = ["TaskRecord"]
