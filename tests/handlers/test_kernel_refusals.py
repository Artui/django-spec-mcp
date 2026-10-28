"""The kernel path's refusal map: total, ordered, and each row answering with its own wire body.

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

The wire body a client reads for a denial, a missing field and a service refusal
is also written out here as a literal:
``test_a_denial_is_answered_with_insufficient_permission``,
``test_a_missing_field_carries_the_validators_detail_in_the_servers_own_wording`` and
``test_a_service_refusal_is_answered_with_its_wire_body``.
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
    def service(**_: Any) -> None:
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


@dataclass
class _Account:
    """What a service asking for an ``account`` declares, so the answer is an argument it takes."""

    account: str = ""


def _service(exc: BaseException) -> ServiceSpec:
    return ServiceSpec(
        service=_raising(exc),
        validator=DataclassValidator(_Account),
        permissions=[Unrestricted()],
    )


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
    server.register_tool(
        ServiceSpec(
            service=lambda *, data: data,
            many=True,
            validator=DataclassValidator(_Line),
            permissions=[Unrestricted()],
        ),
        name="many",
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
    assert error["message"] == "Missing required argument(s): `number`."
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
    # The kernel's reading order: the rows by index, then the list's own message.
    assert list(error["detail"]["items"]) == ["1", "non_field_errors"]


def test_a_many_calls_refused_rows_are_listed_in_index_order(kernel: KernelClient) -> None:
    # Rows 2 and 10 fail: the text a model reads lists them as the kernel does,
    # by number, where a sort of the stringified keys would put "10" first.
    lines = [{"number": str(n), "amount_cents": 1} for n in range(12)]
    lines[2]["amount_cents"] = "x"
    lines[10]["amount_cents"] = "x"

    error = _error(kernel.call("many", {"items": lines}))

    assert list(error["detail"]["items"]) == ["2", "10"]


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


def _refused(error: dict[str, Any]) -> dict[str, Any]:
    """The JSON-RPC body of a tool result refusing the call with ``error``."""
    text = json.dumps({"error": error}, indent=2)
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [{"type": "text", "text": text}],
            "isError": True,
            "resultType": "complete",
        },
    }


@pytest.mark.parametrize(
    ("exc", "name", "drfs_served"),
    [
        (
            ServiceError("The ledger is closed"),
            "closed",
            _refused({"message": "The ledger is closed", "type": "service_error"}),
        ),
        (
            ActionUnavailable("Already sent", code="already_sent"),
            "sent",
            _refused({"code": "already_sent", "message": "Already sent", "type": "service_error"}),
        ),
        (
            ServiceValidationError("Too late to amend"),
            "late",
            _refused(
                {
                    "detail": "Too late to amend",
                    "message": "Too late to amend",
                    "type": "validation_error",
                }
            ),
        ),
        (
            AdditionalInputRequired("Which account?", schema=_ACCOUNT),
            "asks",
            _refused(
                {"message": "Which account?", "requestedInput": _ACCOUNT, "type": "input_required"}
            ),
        ),
    ],
)
def test_a_service_refusal_is_answered_with_its_wire_body(
    client: Client, exc: ServiceError, name: str, drfs_served: dict[str, Any]
) -> None:
    """``drfs_served`` is what a tool whose service raises ``exc`` serves."""
    assert _served(client, _build(), name) == drfs_served


def test_a_denial_is_answered_with_insufficient_permission(client: Client) -> None:
    """A tool whose permission check denies serves this body."""
    assert _served(client, _build(), "denied") == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32006, "message": "Insufficient permission"},
    }


def test_a_missing_field_carries_the_validators_detail_in_the_servers_own_wording(
    client: Client,
) -> None:
    """A field the call left out is refused by the kernel's validator with its
    per-field detail. The kernel reads the field as left out, so the message names
    it in the server's ``missing_arguments`` wording rather than ``Invalid arguments``."""
    by_kernel = _error(_served(client, _build(), "validated", {}))
    assert by_kernel["detail"] == {"number": ["This field is required."]}
    assert by_kernel["type"] == "validation_error"
    assert by_kernel["message"] == "Missing required argument(s): `number`."


def _served(
    client: Client, server: MCPServer, name: str, arguments: dict[str, Any] | None = None
) -> dict[str, Any]:
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        caller = KernelClient(client)
        caller.initialize()
        return caller.call(name, arguments or {})
