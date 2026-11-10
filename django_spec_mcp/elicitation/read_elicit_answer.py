from __future__ import annotations

from typing import Any

from django_spec_mcp.elicitation.types.elicit_answer import ElicitAnswer
from django_spec_mcp.types.elicit_action import ElicitAction
from django_spec_mcp.types.elicitation_key import ELICITATION_KEY


def read_elicit_answer(params: dict[str, Any]) -> ElicitAnswer | None:
    """Pull this package's answer out of a retry's ``inputResponses``."""
    responses: Any = params.get("inputResponses")
    if not isinstance(responses, dict):
        return None
    answer: Any = responses.get(ELICITATION_KEY)
    if not isinstance(answer, dict):
        return None
    try:
        action = ElicitAction(answer.get("action"))
    except ValueError:
        return None
    content: Any = answer.get("content")
    return ElicitAnswer(action=action, content=content if isinstance(content, dict) else {})


__all__ = ["read_elicit_answer"]
