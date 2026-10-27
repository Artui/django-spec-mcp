"""The kernel path's refusal map: total, ordered, and each row answering as drf-services does.

Every row has a test asserting its own answer and asserting that it is not the
answer of the row the refusal falls to with that row deleted, so mutating a row
out fails a named test even where the map stays total:

- ``NotPermitted`` / ``PrincipalUnavailable`` (fall to ``DispatchError``):
  ``test_a_denied_principal_is_insufficient_permission``,
  ``test_a_deactivated_principal_is_insufficient_permission``;
- ``InvalidArguments`` (falls to ``DispatchError``):
  ``test_invalid_arguments_carry_their_per_field_detail``, and for a ``many`` call
  refused by row and as a whole, whose detail mixes integer indices with
  ``non_field_errors``:
  ``test_a_many_call_refused_by_row_and_as_a_whole_is_a_validation_result``;
- ``DispatchError`` (unanswered without it):
  ``test_a_bare_dispatch_error_is_a_validation_result`` and the totality test;
- ``AdditionalInputRequired`` (falls to ``ServiceError``):
  ``test_additional_input_required_asks_for_input``;
- ``ServiceValidationError`` (falls to ``ServiceError``):
  ``test_a_service_validation_error_is_a_validation_result``;
- ``ServiceError`` (unanswered without it): ``test_a_service_error_is_a_service_result``
  and the totality test.

Each row with a drf-services counterpart is also compared with what a drf-services
tool serves for it: ``test_a_denial_is_answered_as_the_drf_services_path_answers_it``,
``test_invalid_arguments_are_answered_as_the_drf_services_path_answers_them`` and
``test_a_service_refusal_is_answered_as_the_drf_services_path_answers_it``. A bare
``DispatchError`` and a deactivated principal have none: drf-services raises neither.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django_service_specs import (
    NON_FIELD_ERRORS,
    ActionUnavailable,
    AdditionalInputRequired,
    DataclassValidator,
    DispatchError,
    InvalidArguments,
    PermissionCheck,
    SelectorKind,
    SelectorSpec,
    ServiceError,
    ServiceSpec,
    ServiceValidationError,
    Unrestricted,
    unanswered_refusals,
)
from rest_framework import serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.types.service_spec import ServiceSpec as DrfServiceSpec

from django_spec_mcp import MCPServer
from django_spec_mcp.handlers.kernel_refusals import KERNEL_REFUSALS
from tests.kernel_client import KernelClient
from tests.testapp.kernel_shapes import InvoiceInput
from tests.testapp.mcp import build_kernel_server
from tests.testapp.urlconf_for import urlconf_for

pytestmark = pytest.mark.django_db(transaction=True)


_ACCOUNT: dict[str, Any] = {"account": {"type": "string"}}


class _Deny(PermissionCheck):
    def has_permission(self, *args: Any, **kwargs: Any) -> bool:
        return False


def _raising(exc: BaseException) -> Any:
    def service() -> None:
        raise exc

    return service


@dataclass
class _Line:
    number: str
    amount_cents: int


class _CappedLines(DataclassValidator):
    """A Validator that refuses the list it is handed as a whole, as a cap on its length would."""

    def validated_many(
        self, rows: Mapping[int, Mapping[str, Any]], *args: Any, **kwargs: Any
    ) -> Any:
        raise InvalidArguments({NON_FIELD_ERRORS: ["Too many lines."]})


def _service(exc: BaseException) -> ServiceSpec:
    return ServiceSpec(service=_raising(exc), permissions=[Unrestricted()])


def _build() -> MCPServer:
    server = build_kernel_server()
    server.register_tool(ServiceSpec(service=lambda: None, permissions=[_Deny()]), name="denied")
    server.register_tool(
        ServiceSpec(
            service=lambda **_: None,
            validator=DataclassValidator(InvoiceInput),
            permissions=[Unrestricted()],
        ),
        name="validated",
    )
    server.register_tool(
        ServiceSpec(
            service=lambda *, data: data,
            many=True,
            validator=_CappedLines(_Line),
            permissions=[Unrestricted()],
        ),
        name="bulk",
    )
    server.register_tool(_service(DispatchError("the call cannot run")), name="bare")
    server.register_tool(
        _service(AdditionalInputRequired("Which account?", schema=_ACCOUNT)),
        name="asks",
    )
    server.register_tool(_service(ServiceValidationError("Too late to amend")), name="late")
    server.register_tool(_service(ServiceError("The ledger is closed")), name="closed")
    server.register_tool(
        _service(ActionUnavailable("Already sent", code="already_sent")), name="sent"
    )
    server.register_tool(
        SelectorSpec(
            kind=SelectorKind.LIST, selector=_raising(_http404()), permissions=[Unrestricted()]
        ),
        name="gone",
    )
    return server


def _http404() -> BaseException:
    from django.http import Http404

    return Http404("no parent")


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def kernel(request: pytest.FixtureRequest, client: Client) -> Iterator[KernelClient]:
    with override_settings(ROOT_URLCONF=urlconf_for(_build(), is_async=request.param)):
        caller = KernelClient(client)
        caller.initialize()
        yield caller


def _error(body: dict[str, Any]) -> dict[str, Any]:
    result = body["result"]
    assert result["isError"] is True
    return json.loads(result["content"][0]["text"])["error"]


def test_the_map_answers_every_kernel_refusal() -> None:
    assert unanswered_refusals(tuple(KERNEL_REFUSALS.items())) == ()


def test_a_denied_principal_is_insufficient_permission(kernel: KernelClient) -> None:
    body = kernel.call("denied")
    assert body["error"] == {"code": -32006, "message": "Insufficient permission"}
    assert "result" not in body


def test_a_deactivated_principal_is_insufficient_permission(client: Client) -> None:
    get_user_model().objects.create_user(username="gone", is_active=False)
    with override_settings(ROOT_URLCONF=urlconf_for(_build())):
        caller = KernelClient(client, user="gone")
        caller.initialize()
        body = caller.call("invoices.get", {"pk": 1})
    assert body["error"] == {"code": -32006, "message": "Insufficient permission"}


def test_invalid_arguments_carry_their_per_field_detail(kernel: KernelClient) -> None:
    error = _error(kernel.call("validated", {"amount_cents": 3}))
    assert error["type"] == "validation_error"
    assert error["message"] == "Invalid arguments"
    # The refusal's own map, not the non-field sentence a bare DispatchError gets.
    assert set(error["detail"]) == {"number"}


def test_a_many_call_refused_by_row_and_as_a_whole_is_a_validation_result(
    kernel: KernelClient,
) -> None:
    # Row 1 fails the shape check and the Validator refuses the list it is handed,
    # so the kernel's detail node mixes the integer index with ``non_field_errors``,
    # which a sorted-key encoder cannot order: it answered a 500.
    lines = [{"number": "A", "amount_cents": 1}, {"number": "B", "amount_cents": "x"}]

    body = kernel.call("bulk", {"items": lines})

    error = _error(body)
    assert error["type"] == "validation_error"
    assert error["detail"] == {
        "items": {
            "1": {"amount_cents": ["Expected an integer."]},
            "non_field_errors": ["Too many lines."],
        }
    }


def test_a_bare_dispatch_error_is_a_validation_result(kernel: KernelClient) -> None:
    assert _error(kernel.call("bare")) == {
        "type": "validation_error",
        "message": "Invalid arguments",
        "detail": {"non_field_errors": ["the call cannot run"]},
    }


def test_additional_input_required_asks_for_input(kernel: KernelClient) -> None:
    # A client that cannot be asked gets MRTR's degraded answer, which names the
    # input wanted, where the service-error fallback would not.
    assert _error(kernel.call("asks")) == {
        "type": "input_required",
        "message": "Which account?",
        "requestedInput": _ACCOUNT,
    }


def test_a_service_validation_error_is_a_validation_result(kernel: KernelClient) -> None:
    error = _error(kernel.call("late"))
    assert error["type"] == "validation_error"
    assert error["message"] == "Too late to amend"


def test_a_service_error_is_a_service_result(kernel: KernelClient) -> None:
    assert _error(kernel.call("closed")) == {
        "type": "service_error",
        "message": "The ledger is closed",
    }


def test_an_action_unavailable_carries_its_code(kernel: KernelClient) -> None:
    assert _error(kernel.call("sent")) == {
        "type": "service_error",
        "message": "Already sent",
        "code": "already_sent",
    }


def test_a_list_not_found_is_worded_as_a_list(kernel: KernelClient) -> None:
    assert _error(kernel.call("gone")) == {"type": "not_found", "message": "gone: not found"}


def test_a_retrieve_not_found_is_worded_as_a_missing_instance(kernel: KernelClient) -> None:
    assert _error(kernel.call("invoices.get", {"pk": 999})) == {
        "type": "not_found",
        "message": "invoices.get: no matching instance found",
    }


@pytest.mark.parametrize(
    ("exc", "name"),
    [
        (ServiceError("The ledger is closed"), "closed"),
        (ActionUnavailable("Already sent", code="already_sent"), "sent"),
        (ServiceValidationError("Too late to amend"), "late"),
        (AdditionalInputRequired("Which account?", schema=_ACCOUNT), "asks"),
    ],
)
def test_a_service_refusal_is_answered_as_the_drf_services_path_answers_it(
    client: Client, exc: ServiceError, name: str
) -> None:
    drfs = MCPServer(name="drfs", auth_backend=build_kernel_server()._auth_backend)
    drfs.register_service_tool(name=name, spec=DrfServiceSpec(service=_raising(exc)))
    assert _served(client, _build(), name) == _served(client, drfs, name)


class _DrfDeny(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


class _NumberSerializer(serializers.Serializer):
    number = serializers.CharField()


def test_a_denial_is_answered_as_the_drf_services_path_answers_it(client: Client) -> None:
    drfs = MCPServer(name="drfs", auth_backend=build_kernel_server()._auth_backend)
    drfs.register_service_tool(
        name="denied", spec=DrfServiceSpec(service=lambda: None, permission_classes=[_DrfDeny])
    )
    assert _served(client, _build(), "denied") == _served(client, drfs, "denied")


def test_invalid_arguments_are_answered_as_the_drf_services_path_answers_them(
    client: Client,
) -> None:
    """The same missing field, refused by a serializer there and the kernel's
    validator here, answers the same error, per-field detail and wording included."""
    drfs = MCPServer(name="drfs", auth_backend=build_kernel_server()._auth_backend)
    drfs.register_service_tool(
        name="validated",
        spec=DrfServiceSpec(service=lambda **_: None, input_serializer=_NumberSerializer),
    )
    by_drfs = _error(_served(client, drfs, "validated", {"data": {}}))
    by_kernel = _error(_served(client, _build(), "validated", {}))
    assert by_kernel == by_drfs
    assert by_kernel["detail"] == {"number": ["This field is required."]}


def _served(
    client: Client, server: MCPServer, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client)
        caller.initialize()
        return caller.call(name, arguments or {})
