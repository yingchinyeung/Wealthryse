from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from ibkr_etl.admin_chat import (
    admin_conversation_title,
    admin_model_history,
    admin_transcript_markdown,
    admin_transcript_payload,
    generate_admin_chat_response,
)
from ibkr_etl.ai_analyst import AIAnalystConfig, AIAnalystError


class _Responses:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            status="completed",
            output_text=json.dumps(self.payload),
            usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 21}),
        )


class _Client:
    def __init__(self, payload):
        self.responses = _Responses(payload)


class AdminChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AIAnalystConfig(
            True,
            "https://example.test",
            "gpt",
            "southeastasia",
            "apac-data-zone",
            "identity",
        )
        self.sources = [
            {"source_id": "latest-supervisor-run", "data": {"status": "healthy"}},
            {"source_id": "controlled-action-catalog", "data": []},
        ]

    def _payload(self, action: str = "none") -> dict:
        return {
            "title": "System status",
            "message": "The latest supervisor run is healthy.",
            "status_points": [
                {"text": "Supervisor healthy", "source_id": "latest-supervisor-run"}
            ],
            "proposed_action_type": action,
            "action_reason": "Owner requested a refresh." if action != "none" else "",
            "requires_owner_approval": action != "none",
            "limitations": ["No direct model execution."],
        }

    def test_model_response_is_grounded_and_can_only_propose_preview(self) -> None:
        client = _Client(self._payload("refresh_risk_free_rate"))
        result, usage = generate_admin_chat_response(
            self.config,
            owner_message="Refresh the Treasury risk-free rate",
            structured_sources=self.sources,
            client_factory=lambda _: client,
        )
        self.assertEqual(result["proposed_action_type"], "refresh_risk_free_rate")
        self.assertTrue(result["requires_owner_approval"])
        self.assertEqual(usage["total_tokens"], 21)
        instructions = client.responses.kwargs["instructions"]
        self.assertIn("Never claim that you executed a change", instructions)
        self.assertIn("Never tell the owner that replying in chat approves", instructions)
        self.assertIn("untrusted", instructions)

    def test_model_cannot_invent_source_or_bypass_approval(self) -> None:
        payload = self._payload()
        payload["status_points"][0]["source_id"] = "invented"
        with self.assertRaises(AIAnalystError) as raised:
            generate_admin_chat_response(
                self.config,
                owner_message="Status?",
                structured_sources=self.sources,
                client_factory=lambda _: _Client(payload),
            )
        self.assertEqual(raised.exception.code, "invented_admin_source")
        payload = self._payload("refresh_provisional_benchmarks")
        payload["requires_owner_approval"] = False
        with self.assertRaises(AIAnalystError) as raised:
            generate_admin_chat_response(
                self.config,
                owner_message="Refresh benchmarks",
                structured_sources=self.sources,
                client_factory=lambda _: _Client(payload),
            )
        self.assertEqual(raised.exception.code, "invalid_admin_approval_state")

    def test_history_is_bounded_and_role_mapped(self) -> None:
        messages = [
            {"role": "owner" if index % 2 == 0 else "administrator", "content_text": str(index)}
            for index in range(20)
        ]
        history = admin_model_history(messages)
        self.assertEqual(len(history), 12)
        self.assertEqual(history[0]["content"], "8")
        self.assertEqual(history[0]["role"], "user")

    def test_transcript_exports_complete_conversation(self) -> None:
        conversation = {
            "conversation_id": "11111111-1111-1111-1111-111111111111",
            "title": "System health",
            "initiated_by": "administrator",
            "status": "active",
            "created_at": "2026-09-24T00:00:00Z",
            "updated_at": "2026-09-24T00:01:00Z",
        }
        messages = [
            {
                "message_id": "22222222-2222-2222-2222-222222222222",
                "sequence_no": 1,
                "role": "administrator",
                "message_kind": "proactive_update",
                "content_text": "Monitoring is active.",
                "response_json": self._payload(),
                "created_at": "2026-09-24T00:00:00Z",
            }
        ]
        payload = admin_transcript_payload(conversation, messages)
        markdown = admin_transcript_markdown(conversation, messages)
        self.assertEqual(payload["messages"][0]["content"], "Monitoring is active.")
        self.assertIn("AI System Administrator", markdown)
        self.assertIn("Monitoring is active.", markdown)

    def test_title_is_compact(self) -> None:
        self.assertEqual(admin_conversation_title("  system   health  "), "system health")
        self.assertLessEqual(len(admin_conversation_title("x" * 100)), 72)


if __name__ == "__main__":
    unittest.main()
