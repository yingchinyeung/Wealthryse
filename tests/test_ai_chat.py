from __future__ import annotations

import json
import unittest

from ibkr_etl.ai_chat import (
    MAX_HISTORY_MESSAGES,
    analysis_json,
    chart_csv,
    chart_svg,
    conversation_title,
    model_history,
    response_digest,
    transcript_markdown,
    transcript_payload,
)


class AIChatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation = {
            "conversation_id": "1dfe17bb-bfc0-4237-97b2-55180d6d2e34",
            "subject_id": "U16088571",
            "title": "Performance review",
            "status": "active",
            "created_at": "2026-09-24T01:00:00Z",
            "updated_at": "2026-09-24T01:01:00Z",
        }
        self.response = {
            "narrative": {
                "title": "Grounded analysis",
                "summary": "Performance is based on validated data.",
                "key_points": [],
                "risks_and_caveats": [],
            },
            "deterministic_facts": [
                {
                    "label": "Total return",
                    "value": 0.12,
                    "format": "percent",
                    "as_of": "2026-09-18",
                }
            ],
            "visualizations": [
                {
                    "id": "nav-history",
                    "title": "NAV <history>",
                    "y_label": "NAV (USD)",
                    "lines": [
                        {"key": "portfolio", "label": "Portfolio & NAV", "color": "#2646a6"}
                    ],
                    "series": [
                        {"date": "2026-09-17", "portfolio": 100.0},
                        {"date": "2026-09-18", "portfolio": 101.5},
                    ],
                }
            ],
        }
        self.messages = [
            {
                "message_id": "a",
                "sequence_no": 1,
                "role": "user",
                "content_text": "Show performance",
                "as_of_date": "2026-09-18",
                "benchmark_id": "GSPC",
                "created_at": "2026-09-24T01:00:00Z",
                "response_json": None,
            },
            {
                "message_id": "b",
                "sequence_no": 2,
                "role": "assistant",
                "content_text": "Grounded analysis",
                "as_of_date": "2026-09-18",
                "benchmark_id": "GSPC",
                "created_at": "2026-09-24T01:01:00Z",
                "response_json": self.response,
            },
        ]

    def test_title_is_normalized_and_bounded(self) -> None:
        self.assertEqual(conversation_title("  Show   risk  "), "Show risk")
        self.assertEqual(conversation_title(""), "New conversation")
        self.assertLessEqual(len(conversation_title("x" * 200)), 72)

    def test_model_history_is_role_filtered_bounded_and_truncated(self) -> None:
        messages = [
            {"role": "system", "content_text": "never include"},
            *[
                {"role": "user" if index % 2 == 0 else "assistant", "content_text": str(index) * 5000}
                for index in range(MAX_HISTORY_MESSAGES + 3)
            ],
        ]
        history = model_history(messages)
        self.assertEqual(len(history), MAX_HISTORY_MESSAGES)
        self.assertTrue(all(item["role"] in {"user", "assistant"} for item in history))
        self.assertTrue(all(len(item["content"]) <= 4000 for item in history))
        self.assertNotIn("never include", json.dumps(history))

    def test_transcript_exports_include_saved_analysis(self) -> None:
        payload = transcript_payload(self.conversation, self.messages)
        self.assertEqual(payload["conversation"]["subject_id"], "U16088571")
        self.assertEqual(payload["messages"][1]["analysis"], self.response)
        markdown = transcript_markdown(self.conversation, self.messages)
        self.assertIn("# Performance review", markdown)
        self.assertIn("## AI Portfolio Analyst", markdown)
        self.assertIn("Total return: 0.12", markdown)
        self.assertIn("NAV <history> (nav-history)", markdown)

    def test_chart_exports_are_portable_and_escape_svg_text(self) -> None:
        chart = self.response["visualizations"][0]
        csv_text = chart_csv(chart)
        self.assertEqual(
            csv_text,
            "date,portfolio\n2026-09-17,100.0\n2026-09-18,101.5\n",
        )
        svg = chart_svg(chart)
        self.assertIn("<polyline", svg)
        self.assertIn("NAV &lt;history&gt;", svg)
        self.assertIn("Portfolio &amp; NAV", svg)
        self.assertNotIn("NAV <history>", svg)

    def test_analysis_json_and_digest_are_deterministic(self) -> None:
        parsed = json.loads(analysis_json(self.response))
        self.assertEqual(parsed, self.response)
        self.assertEqual(response_digest(self.response), response_digest(dict(self.response)))


if __name__ == "__main__":
    unittest.main()
