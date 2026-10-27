"""``ResultType`` - the discriminator every result carries from 2026-07-28 onward."""

from __future__ import annotations

from enum import Enum


class ResultType(str, Enum):
    """The discriminator every result carries from ``2026-07-28`` onward.

    A **MUST** for servers implementing that revision and harmless before it —
    a legacy result object is an open shape, and a client on an older revision
    reads an absent ``resultType`` as ``complete`` — so it is emitted
    unconditionally rather than era-branched.

    Attributes:
        COMPLETE: The result is the answer.
        INPUT_REQUIRED: The result asks the client for input and expects the
            original request to be retried with the answers. Nothing here
            produces one yet; the vocabulary is the spec's.
    """

    COMPLETE = "complete"
    INPUT_REQUIRED = "input_required"
    TASK = "task"
    """A durable handle instead of the result — see ``django_spec_mcp.tasks``.

    The spec types ``ResultType`` as ``"complete" | "input_required" | string``,
    the open tail being how extensions add their own. Servers **MUST NOT** set
    this on any result other than a ``CreateTaskResult``, which is why nothing
    stamps it centrally the way ``COMPLETE`` is stamped — the task handler sets
    it explicitly and no other handler can reach it.
    """
