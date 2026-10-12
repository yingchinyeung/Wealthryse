from __future__ import annotations

import csv
import io
import json
import math
from html import escape
from typing import Any, Mapping, Sequence

from .util import canonical_json


MAX_CONVERSATION_MESSAGES = 100
MAX_HISTORY_MESSAGES = 12


def conversation_title(question: str) -> str:
    value = " ".join(question.split()).strip()
    if not value:
        return "New conversation"
    return value if len(value) <= 72 else value[:69].rstrip() + "…"


def assistant_plain_text(response: Mapping[str, Any]) -> str:
    narrative = response.get("narrative") or {}
    lines = [str(narrative.get("title") or "Portfolio analysis")]
    summary = str(narrative.get("summary") or "").strip()
    if summary:
        lines.extend(("", summary))
    for item in narrative.get("key_points") or []:
        lines.append(f"- {item.get('text') or ''}".rstrip())
    for item in narrative.get("risks_and_caveats") or []:
        lines.append(f"- Caveat: {item.get('text') or ''}".rstrip())
    return "\n".join(lines).strip()[:20000]


def model_history(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    history: list[dict[str, str]] = []
    for message in messages[-MAX_HISTORY_MESSAGES:]:
        role = str(message.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = str(message.get("content_text") or "").strip()
        if content:
            history.append({"role": role, "content": content[:4000]})
    return history


def transcript_payload(
    conversation: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "conversation": {
            "conversation_id": str(conversation["conversation_id"]),
            "subject_id": str(conversation["subject_id"]),
            "title": str(conversation["title"]),
            "status": str(conversation["status"]),
            "created_at": str(conversation["created_at"]),
            "updated_at": str(conversation["updated_at"]),
        },
        "messages": [
            {
                "message_id": str(message["message_id"]),
                "sequence_no": int(message["sequence_no"]),
                "role": str(message["role"]),
                "content": str(message["content_text"]),
                "as_of": (
                    str(message["as_of_date"]) if message.get("as_of_date") else None
                ),
                "benchmark_id": message.get("benchmark_id"),
                "created_at": str(message["created_at"]),
                "analysis": (
                    message.get("response_json")
                    if str(message["role"]) == "assistant"
                    else None
                ),
            }
            for message in messages
        ],
    }


def transcript_markdown(
    conversation: Mapping[str, Any], messages: Sequence[Mapping[str, Any]]
) -> str:
    lines = [
        f"# {conversation['title']}",
        "",
        f"- Account: {conversation['subject_id']}",
        f"- Created: {conversation['created_at']}",
        f"- Updated: {conversation['updated_at']}",
        "",
    ]
    for message in messages:
        label = "User" if str(message["role"]) == "user" else "AI Portfolio Analyst"
        lines.extend(
            (
                f"## {label}",
                "",
                str(message["content_text"]),
                "",
            )
        )
        if str(message["role"]) == "assistant":
            response = message.get("response_json") or {}
            facts = response.get("deterministic_facts") or []
            if facts:
                lines.extend(("### Validated figures", ""))
                for fact in facts:
                    lines.append(
                        f"- {fact.get('label')}: {fact.get('value')}"
                        f" ({fact.get('format')}; as of {fact.get('as_of')})"
                    )
                lines.append("")
            charts = response.get("visualizations") or []
            if charts:
                lines.extend(("### Generated charts", ""))
                for chart in charts:
                    lines.append(f"- {chart.get('title')} ({chart.get('id')})")
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def chart_csv(chart: Mapping[str, Any]) -> str:
    lines = list(chart.get("lines") or [])
    keys = [str(line.get("key")) for line in lines if line.get("key")]
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", *keys])
    for row in chart.get("series") or []:
        writer.writerow([row.get("date"), *(row.get(key) for key in keys)])
    return output.getvalue()


def chart_svg(chart: Mapping[str, Any]) -> str:
    series = list(chart.get("series") or [])
    lines = [line for line in chart.get("lines") or [] if line.get("key")]
    if not series or not lines:
        raise ValueError("The chart has no downloadable series")
    values = [
        float(row[line["key"]])
        for line in lines
        for row in series
        if row.get(line["key"]) is not None
        and math.isfinite(float(row[line["key"]]))
    ]
    if not values:
        raise ValueError("The chart has no finite values")
    width, height = 1200, 520
    left, right, top, bottom = 94, 34, 70, 72
    plot_width, plot_height = width - left - right, height - top - bottom
    low, high = min(values), max(values)
    if high <= low:
        high, low = high + 1.0, low - 1.0

    def x_at(index: int) -> float:
        return left + index * plot_width / max(1, len(series) - 1)

    def y_at(value: float) -> float:
        return top + (high - value) * plot_height / (high - low)

    elements = [
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        f'<text x="{left}" y="36" font-family="Arial, sans-serif" font-size="22" font-weight="700" fill="#121925">{escape(str(chart.get("title") or "Portfolio chart"))}</text>',
    ]
    for index in range(6):
        value = high - index * (high - low) / 5
        y = y_at(value)
        elements.extend(
            (
                f'<line x1="{left}" y1="{y:.2f}" x2="{width-right}" y2="{y:.2f}" stroke="#dce3ed" stroke-width="1"/>',
                f'<text x="{left-12}" y="{y+4:.2f}" text-anchor="end" font-family="Arial, sans-serif" font-size="12" fill="#667085">{value:.2f}</text>',
            )
        )
    for line_index, line in enumerate(lines):
        points = []
        for index, row in enumerate(series):
            value = row.get(line["key"])
            if value is not None and math.isfinite(float(value)):
                points.append(f"{x_at(index):.2f},{y_at(float(value)):.2f}")
        color = escape(str(line.get("color") or "#2646a6"), quote=True)
        elements.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        legend_x = left + line_index * 250
        elements.extend(
            (
                f'<line x1="{legend_x}" y1="{height-27}" x2="{legend_x+28}" y2="{height-27}" stroke="{color}" stroke-width="4"/>',
                f'<text x="{legend_x+38}" y="{height-22}" font-family="Arial, sans-serif" font-size="13" fill="#344054">{escape(str(line.get("label") or line["key"]))}</text>',
            )
        )
    for index in range(5):
        position = round(index * (len(series) - 1) / 4)
        x = x_at(position)
        elements.append(
            f'<text x="{x:.2f}" y="{height-bottom+27}" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#667085">{escape(str(series[position].get("date") or ""))}</text>'
        )
    elements.extend(
        (
            f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#475467" stroke-width="1.2"/>',
            f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#475467" stroke-width="1.2"/>',
            f'<text transform="translate(22 {top+plot_height/2}) rotate(-90)" text-anchor="middle" font-family="Arial, sans-serif" font-size="13" fill="#475467">{escape(str(chart.get("y_label") or "Value"))}</text>',
        )
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
        + "".join(elements)
        + "</svg>"
    )


def analysis_json(response: Mapping[str, Any]) -> str:
    return json.dumps(response, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def response_digest(response: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(response).encode("utf-8")).hexdigest()

