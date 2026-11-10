from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

import django_spec_mcp

BRIDGE = pathlib.Path(django_spec_mcp.__file__).parent / "ui" / "bridge.js"
HARNESS = pathlib.Path(__file__).parent / "bridge_harness.mjs"

INITIALIZED = "ui/notifications/initialized"


@pytest.fixture(scope="module")
def scenarios() -> dict[str, Any]:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required to exercise bridge.js and is missing on this runner")
        pytest.skip("node is not installed; bridge.js behaviour is unverified here")
    completed = subprocess.run(
        [node, str(HARNESS), str(BRIDGE)],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


class TestTheHandshakeAlwaysCompletes:
    """The reason this file exists."""

    def test_on_a_successful_reply(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["success"]["methods"]

    def test_on_an_error_reply(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["error_reply"]["methods"]

    def test_on_no_reply_at_all(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["no_reply"]["methods"]

    def test_it_is_sent_exactly_once_when_a_late_reply_follows_the_timeout(
        self, scenarios: dict[str, Any]
    ) -> None:
        """The timeout and the reply are two paths to the same funnel."""
        assert scenarios["timeout_then_reply"]["methods"].count(INITIALIZED) == 1

    def test_a_failed_handshake_leaves_a_visible_explanation(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Completing the handshake is what buys the diagnostic surface: the frame is revealed."""
        assert "host said no" in " ".join(scenarios["error_reply"]["banner"])

    def test_a_reply_that_loses_the_race_with_the_timeout_is_still_applied(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Only the notification must not repeat."""
        result = scenarios["late_reply"]

        assert result["banner_after_timeout"] != []
        assert result["banner_after_reply"] == []
        assert result["theme"] == "dark"
        assert result["protocol_version"] == "2026-01-26"
        assert result["initialized_count"] == 1


class TestDiagnosticsAreOptIn:
    """What the failure says, and to whom."""

    def test_the_frame_is_revealed_either_way(self, scenarios: dict[str, Any]) -> None:
        assert INITIALIZED in scenarios["error_reply_quiet"]["methods"]

    def test_nothing_is_written_into_the_document_by_default(
        self, scenarios: dict[str, Any]
    ) -> None:
        assert scenarios["error_reply_quiet"]["banner"] == []

    def test_the_reason_still_reaches_the_console(self, scenarios: dict[str, Any]) -> None:
        assert "host said no" in " ".join(scenarios["error_reply_quiet"]["logged"])

    def test_an_answered_host_is_not_then_accused_of_not_answering(
        self, scenarios: dict[str, Any]
    ) -> None:
        """The pending timeout has to be cancelled on the first completion."""
        result = scenarios["error_reply_cancels_the_timer"]

        assert len(result["logged"]) == 1
        assert result["banner"] == [
            "This view could not complete the MCP Apps handshake with its host: host said no"
        ]

    def test_a_document_outside_a_host_always_says_so(self, scenarios: dict[str, Any]) -> None:
        """The one unconditional case: there is no host."""
        assert "rendering outside a host" in " ".join(scenarios["unframed"]["banner"])


class TestTheInitializeRequest:
    def test_it_sends_the_field_names_the_schema_requires(self, scenarios: dict[str, Any]) -> None:
        """``appInfo`` and ``appCapabilities``, not ``clientInfo`` and ``capabilities``."""
        params = scenarios["success"]["initialize_params"]
        assert set(params) == {"appInfo", "appCapabilities", "protocolVersion"}
        assert params["protocolVersion"] == "2026-01-26"


class TestHostContext:
    def test_the_theme_locale_and_style_variables_are_applied(
        self, scenarios: dict[str, Any]
    ) -> None:
        result = scenarios["success"]
        assert result["theme"] == "dark"
        assert result["lang"] == "fr-FR"
        assert result["css_variables"] == {"--text-primary": "#eee"}

    def test_the_negotiated_protocol_version_is_kept(self, scenarios: dict[str, Any]) -> None:
        assert scenarios["success"]["protocol_version"] == "2026-01-26"


class TestDataDelivery:
    def test_handlers_assigned_during_parsing_receive_input_and_results(
        self, scenarios: dict[str, Any]
    ) -> None:
        """The bridge waits for DOMContentLoaded so a fragment's inline script registers first."""
        seen = scenarios["tool_result"]["seen"]
        assert seen[0] == ["input", {"ordering": "-total"}]
        assert seen[1][0] == {"results": [{"id": 1}]}

    def test_a_throwing_handler_does_not_take_the_bridge_down(
        self, scenarios: dict[str, Any]
    ) -> None:
        """A view whose render raises must still say so, and still report its size."""
        result = scenarios["handler_throws"]
        assert "render blew up" in " ".join(result["banner"])
        assert "ui/notifications/size-changed" in result["methods"]


class TestSizing:
    def test_a_view_can_name_the_element_to_measure_width_from(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Measuring the root moves the overflow trap up a level rather than removing it."""
        result = scenarios["measure_width_from"]

        assert result["before_width"] == 320
        assert result["after"]["width"] == 1400
        assert result["after"]["height"] == 240


class TestRequestsFromTheHost:
    def test_an_unimplemented_method_is_answered_rather_than_ignored(
        self, scenarios: dict[str, Any]
    ) -> None:
        """An unanswered request leaves the host waiting on a promise that never settles."""
        answers = {a["id"]: a for a in scenarios["unknown_request"]["answers"]}
        assert answers[99]["error"]["code"] == -32601

    def test_teardown_is_acknowledged(self, scenarios: dict[str, Any]) -> None:
        answers = {a["id"]: a for a in scenarios["unknown_request"]["answers"]}
        assert answers[100]["result"] == {}


class TestOutsideAHost:
    def test_a_directly_opened_document_says_so_and_posts_nothing(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Opening the view in a browser is how a project develops one."""
        result = scenarios["unframed"]
        assert result["posted"] == 0
        assert "rendering outside a host" in " ".join(result["banner"])


class TestCallingBack:
    def test_call_tool_goes_out_as_an_ordinary_tools_call_request(
        self, scenarios: dict[str, Any]
    ) -> None:
        """Which is what makes the server's permissions and rate limits apply to it unchanged."""
        result = scenarios["call_tool"]
        assert result["method"] == "tools/call"
        assert result["params"] == {"name": "list_invoices", "arguments": {"ordering": "-total"}}
        assert result["has_id"] is True
