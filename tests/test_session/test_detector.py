"""Tests for session detection."""

import pytest
from condense.session.detector import detect_session


class TestSessionDetector:
    def test_detect_session_from_messages(self):
        """Session is detected from system prompt + first user message."""
        request = {
            "model": "gpt-4o",
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello there!"},
            ],
        }
        session_id, turn = detect_session(request, "api-key-hash")
        assert session_id is not None
        assert len(session_id) == 16
        assert turn == 1

    def test_same_conversation_same_session(self):
        """Same system prompt + first user message = same session."""
        request1 = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        }
        request2 = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "How are you?"},
            ],
        }
        sid1, _ = detect_session(request1, "key-hash")
        sid2, _ = detect_session(request2, "key-hash")
        assert sid1 == sid2

    def test_different_api_keys_different_sessions(self):
        """Different API keys produce different session IDs."""
        request = {
            "messages": [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "Hi"},
            ],
        }
        sid1, _ = detect_session(request, "key-hash-1")
        sid2, _ = detect_session(request, "key-hash-2")
        assert sid1 != sid2

    def test_turn_count(self):
        """Turn count reflects number of user messages."""
        request = {
            "messages": [
                {"role": "system", "content": "Be helpful."},
                {"role": "user", "content": "First"},
                {"role": "assistant", "content": "Response 1"},
                {"role": "user", "content": "Second"},
                {"role": "assistant", "content": "Response 2"},
                {"role": "user", "content": "Third"},
            ],
        }
        _, turn = detect_session(request, "key-hash")
        assert turn == 3

    def test_no_messages(self):
        """No messages returns None session."""
        request = {"model": "gpt-4o", "messages": []}
        session_id, turn = detect_session(request, "key-hash")
        assert session_id is None
        assert turn == 0

    def test_only_user_message(self):
        """Session can be detected from just a user message (no system prompt)."""
        request = {
            "messages": [{"role": "user", "content": "Hello"}],
        }
        session_id, turn = detect_session(request, "key-hash")
        assert session_id is not None
        assert turn == 1
