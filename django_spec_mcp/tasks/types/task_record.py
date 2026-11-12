from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from django_spec_mcp.protocol.types.task import Task
from django_spec_mcp.types.task_status import TaskStatus


@dataclass(frozen=True)
class TaskRecord:
    """What a store holds: the wire
    [`Task`][django_spec_mcp.protocol.types.task.Task] plus what a worker needs.

    A *superset* rather than a parallel type. The extra fields never reach the
    client — no task message has a slot for them — but they are why a task can
    outlive the request that created it: the worker that finishes it shares
    nothing with that request except this record.

    **The scopes are stored, and that is the point.** Without them the worker
    rebuilds a token that proves nothing, and every binding with ``scopes=``
    denies the call it had already been authorized for — silently, long after
    the client was told the work had started.

    ``TokenInfo.raw`` is **not** stored: it is backend-defined credential
    material, and persisting it would put that in a cache with a week-long
    fallback TTL. A rehydrated token has ``raw=None``, so a permission reaching
    into it is one that cannot run as a task.

    **The separation is a security boundary.** ``to_wire`` is the only route
    from a record to a message, which is what stops a principal id or a scope
    list leaking into a response because a field was added to the wrong
    dataclass.

    Attributes:
        task: The wire task — the only part a client ever sees.
        tool_name: The tool the worker replays, verbatim.
        arguments: The arguments it replays, verbatim.
        principal_id: The owner, in the form
            ``principal_for_token``
            already produces for sessions.
        user_pk: Rehydrates the user on the worker.
        scopes: Rebuild the worker's ``TokenInfo`` so its permission checks see
            what the request path saw.
        audience: Rebuilds that ``TokenInfo``'s audience: one URL, or the
            several a token names, held as a tuple whatever sequence was given,
            so a store whose codec turns a tuple into a list (JSON does) hands
            back the same type
            (``test_the_record_holds_a_tuple_so_no_store_changes_its_kind``).
        enqueued: Whether the task reached the executor, so a worker cannot be
            tricked into running it twice. A worker clears it as it claims the
            task, so a task waiting for input is not claimable until
            ``tasks/update`` queues it again.
    """

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
        """Return a copy with fields changed on the embedded
        [`Task`][django_spec_mcp.protocol.types.task.Task].

        Saves every caller a nested ``replace(record, task=replace(record.task,
        ...))``.
        """
        return replace(self, task=replace(self.task, **changes))


__all__ = ["TaskRecord"]
