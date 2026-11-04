"""The NL→SQL step: force Claude to emit one query as a structured tool call.

Why a *forced tool call* instead of "just ask for SQL in text"? A tool with a
typed input schema (`{sql, rationale}`) plus `tool_choice` forcing that tool
guarantees structured fields back — no prose to strip, no markdown fences, no
"Sure! Here's your query:" preamble. It's the cleanest way to get one
machine-usable value out of the model.

The model writes the query ON THE FLY from the schema in schema_context.py;
nothing here is a lookup. This step is deliberately NON-streaming — we need the
whole query before running the guardrail and hitting the DB. Only the final
narrative is streamed.

Shared by the /api/ask router (async) and the eval harness (sync) so both use
the exact same prompt + tool definition.
"""

from typing import Any

from app.schema_context import build_system_prompt

# Forcing this tool means the model MUST return `{sql, rationale}` — the
# structured-output trick applied to text-to-SQL.
EMIT_SQL_TOOL: dict[str, Any] = {
    "name": "emit_sql",
    "description": "Return the single PostgreSQL query that answers the user's question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {
                "type": "string",
                "description": "One PostgreSQL SELECT (or WITH … SELECT). No trailing semicolon.",
            },
            "rationale": {
                "type": "string",
                "description": "One sentence: what the query computes and how.",
            },
        },
        "required": ["sql", "rationale"],
    },
}

SYSTEM_PROMPT = build_system_prompt()


def extract_sql(message: Any) -> tuple[str, str]:
    """Pull (sql, rationale) out of a forced emit_sql tool-use response."""
    for block in message.content:
        if block.type == "tool_use" and block.name == "emit_sql":
            data = block.input
            return data["sql"], data.get("rationale", "")
    raise ValueError("Model did not call emit_sql.")
