"""Auto-detect conversation sessions from request patterns."""

import hashlib
from typing import Optional, Tuple


def detect_session(
    request: dict,
    api_key_hash: str,
) -> Tuple[Optional[str], int]:
    """Auto-detect session ID from request content.

    Sessions are identified by hashing:
      SHA-256(api_key_hash + system_prompt[:200] + first_user_msg[:200])

    This groups requests from the same conversation into a session
    without requiring explicit session IDs.

    Args:
        request: The OpenAI-compatible request body.
        api_key_hash: Hash of the API key for tenant isolation.

    Returns:
        Tuple of (session_id, turn_number). turn_number is 0 for new sessions.
    """
    messages = request.get("messages", [])
    if not messages:
        return None, 0

    # Extract system prompt
    system_prompt = ""
    first_user_msg = ""

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multi-modal: extract text parts
            content = " ".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )

        if role == "system" and not system_prompt:
            system_prompt = content[:200]
        elif role == "user" and not first_user_msg:
            first_user_msg = content[:200]

        if system_prompt and first_user_msg:
            break

    if not system_prompt and not first_user_msg:
        return None, 0

    # Compute session ID
    raw = f"{api_key_hash}{system_prompt}{first_user_msg}"
    session_id = hashlib.sha256(raw.encode()).hexdigest()[:16]

    # Turn number = count of user messages
    turn = sum(1 for m in messages if m.get("role") == "user")

    return session_id, turn
