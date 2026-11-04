"""`POST /api/ask` — natural-language analytics over the property DB.

The pipeline, streamed to the browser as Server-Sent Events:

  1. NL → SQL   : force Claude to emit one query (structured tool call).  -> sql
  2. Guardrail  : single-SELECT gate + forced row cap (app/db.sanitize_sql).
  3. Execute    : read-only txn + statement_timeout (app/db.run_select).  -> rows | error
  4. Narrative  : stream an analyst summary + recommendation over rows.   -> delta… done

Only step 4 streams from the model. Steps 1–3 complete first so the UI can show
the SQL and the result grid before the prose begins.
"""

import json
from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.config import get_settings
from app.db import UnsafeSQLError, run_select, sanitize_sql
from app.nl2sql import EMIT_SQL_TOOL, SYSTEM_PROMPT, extract_sql

router = APIRouter(prefix="/api", tags=["ask"])

_settings = get_settings()
_client = AsyncAnthropic(api_key=_settings.anthropic_api_key)

# Cap rows fed to the narrative model so the prompt stays small and fast even if
# the query legitimately returns the full 500.
_MAX_ROWS_FOR_NARRATIVE = 50

NARRATIVE_SYSTEM = """You are a property-maintenance analyst. You are given a \
user's question, the SQL that answered it, and the result rows. Write a short, \
concrete summary (2-4 sentences) of what the data shows, then a single \
prioritized recommendation prefixed with "Recommendation:". Cite specific \
names and numbers from the rows. Do not restate the SQL. If there are no rows, \
say so plainly.

Format as light Markdown: bold the key figures and names, put the \
"**Recommendation:**" on its own paragraph, and use a short bullet list only \
when comparing three or more items. No headings, no tables."""


class AskRequest(BaseModel):
    question: str


def _sse(data: dict) -> str:
    """Format one Server-Sent Event frame: `data: <json>\\n\\n`."""
    return f"data: {json.dumps(data, default=str)}\n\n"


async def _generate_sql(question: str) -> tuple[str, str]:
    """Step 1: NL → SQL via a forced tool call (non-streaming)."""
    message = await _client.messages.create(
        model=_settings.anthropic_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[EMIT_SQL_TOOL],
        tool_choice={"type": "tool", "name": "emit_sql"},
        messages=[{"role": "user", "content": question}],
    )
    return extract_sql(message)


def _rows_as_text(columns: list[str], rows: list[list]) -> str:
    """Compact tab-separated rendering of the (capped) result set for the model."""
    capped = rows[:_MAX_ROWS_FOR_NARRATIVE]
    header = "\t".join(columns)
    body = "\n".join("\t".join(str(v) for v in r) for r in capped)
    note = "" if len(rows) <= _MAX_ROWS_FOR_NARRATIVE else f"\n… ({len(rows)} rows total)"
    return f"{header}\n{body}{note}"


async def _run_pipeline(question: str) -> AsyncIterator[str]:
    # --- 1. NL → SQL ---
    try:
        sql, rationale = await _generate_sql(question)
    except Exception as exc:  # noqa: BLE001 - surface any model/parse failure
        yield _sse({"type": "error", "message": f"Could not generate SQL: {exc}"})
        return
    yield _sse({"type": "sql", "sql": sql, "rationale": rationale})

    # --- 2 + 3. Guardrail + execute ---
    try:
        safe_sql = sanitize_sql(sql)
        columns, rows = run_select(safe_sql)
    except UnsafeSQLError as exc:
        yield _sse({"type": "error", "message": f"Query blocked by guardrail: {exc}"})
        return
    except Exception as exc:  # noqa: BLE001 - DB/syntax/timeout errors
        yield _sse({"type": "error", "message": f"Query failed: {exc}"})
        return
    yield _sse({"type": "rows", "columns": columns, "rows": rows})

    # --- 4. Narrative (streamed) ---
    user_content = (
        f"Question: {question}\n\n"
        f"SQL:\n{sql}\n\n"
        f"Results ({len(rows)} rows):\n{_rows_as_text(columns, rows)}"
    )
    try:
        async with _client.messages.stream(
            model=_settings.anthropic_model,
            max_tokens=1024,
            system=NARRATIVE_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        ) as stream:
            async for text in stream.text_stream:
                yield _sse({"type": "delta", "text": text})
    except Exception as exc:  # noqa: BLE001 - surface streaming failures
        yield _sse({"type": "error", "message": f"Narrative failed: {exc}"})
        return

    yield _sse({"type": "done"})


@router.post("/ask")
async def ask(req: AskRequest) -> StreamingResponse:
    return StreamingResponse(
        _run_pipeline(req.question),
        media_type="text/event-stream",
        # Disable proxy buffering so frames flush immediately, not in one lump.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
