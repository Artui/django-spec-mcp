"""MCP wiring for the job-status example.

Each operation is a ``django_service_specs`` spec, mounted with
``register_tool`` and ``register_resource``.
"""

from __future__ import annotations

import django_filters
from django_service_specs import (
    DataclassPresenter,
    Parameter,
    Parameters,
    SelectorKind,
    SelectorSpec,
    ServiceSpec,
    Unrestricted,
)

from django_spec_mcp import InMemorySessionStore, MCPServer
from django_spec_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from jobs.models import Job
from jobs.selectors import get_job, list_jobs
from jobs.services import start_job
from jobs.shapes import JobOutput

# Deliberately open, and saying so: a spec declaring no permissions is refused
# at registration. Swap it for a real ``PermissionCheck`` in anything real.
OPEN = [Unrestricted()]

JOB = DataclassPresenter(JobOutput)


class JobFilterSet(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Job.STATUS_CHOICES)
    # The tool's only ordering channel: the kernel reads an ``OrderingFilter``
    # as an array choosing among its public names, so a model asking for the
    # newest jobs first sends ``ordering: ["-created"]``, the vocabulary an
    # HTTP caller uses too.
    ordering = django_filters.OrderingFilter(fields=(("created_at", "created"),))

    class Meta:
        model = Job
        fields = ["status"]


def build_server() -> MCPServer:
    server = MCPServer(
        name="job-status-example",
        version="0.0.1",
        # Dev-only: accepts any caller. A mounted server has no default
        # backend, so production passes one of its own (docs/auth.md).
        auth_backend=AllowAnyBackend(),
        # Fine for single-process dev. The default DjangoCacheSessionStore
        # works across workers.
        session_store=InMemorySessionStore(),
    )

    server.register_tool(
        ServiceSpec(
            service=start_job,
            reads=Parameters(
                [Parameter("duration_seconds", "integer", required=True, minimum=1, maximum=60)]
            ),
            presenter=JOB,
            # The thread-dispatch happens *after* the HTTP response is
            # written, so a transaction here would commit too early. The
            # example uses ``atomic=False`` for that reason; production
            # workers usually publish to a queue, where this concern
            # disappears.
            atomic=False,
            permissions=OPEN,
        ),
        name="jobs.start",
        description="Start a job and return immediately with its id.",
    )

    # A resource is as reachable as a tool, so it declares its permissions the
    # same way. The URI template's ``job_id`` is the spec's read.
    server.register_resource(
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_job,
            reads=Parameters([Parameter("job_id", "string", format="uuid", required=True)]),
            presenter=JOB,
            permissions=OPEN,
        ),
        name="job",
        uri_template="jobs://{job_id}",
        description="Read the latest status of a single job.",
    )

    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_jobs,
            filter_set=JobFilterSet,
            presenter=JOB,
            permissions=OPEN,
        ),
        name="jobs.list",
        description="List jobs, optionally filtered by status, ordered, paginated.",
        paginate=True,
    )

    return server
