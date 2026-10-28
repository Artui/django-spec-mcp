from __future__ import annotations

from django.db.models import QuerySet

from jobs.models import Job


def list_jobs() -> QuerySet[Job]:
    return Job.objects.all()


def get_job(*, job_id: str) -> QuerySet[Job]:
    """The job ``jobs://{job_id}`` names: a RETRIEVE the kernel resolves to one row.

    Returned as a queryset, so a job that does not exist is the kernel's
    not-found rather than an exception of this selector's own.
    """
    return Job.objects.filter(pk=job_id)
