"""Rule engine for model routing decisions."""

import logging
from typing import List, Optional

from condense.config.schema import RoutingRule

logger = logging.getLogger(__name__)


def evaluate_rules(request: dict, rules: List[RoutingRule]) -> Optional[str]:
    """Evaluate routing rules against the request.

    Returns the model to route to, or None if no rules matched.
    """
    for rule in rules:
        if _evaluate_condition(request, rule):
            logger.debug(f"Routing rule matched: {rule.condition} → {rule.model}")
            return rule.model
    return None


def _evaluate_condition(request: dict, rule: RoutingRule) -> bool:
    """Evaluate a single routing condition."""
    if rule.condition == "short_messages":
        return _is_short_message(request, rule.max_chars or 500)
    elif rule.condition == "no_tools":
        return _has_no_tools(request)
    else:
        logger.warning(f"Unknown routing condition: {rule.condition}")
        return False


def _is_short_message(request: dict, max_chars: int) -> bool:
    """Check if total message content is under max_chars."""
    messages = request.get("messages", [])
    total_chars = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    total_chars += len(block.get("text", ""))
    return total_chars <= max_chars


def _has_no_tools(request: dict) -> bool:
    """Check if the request has no tools defined."""
    tools = request.get("tools")
    return not tools or len(tools) == 0
