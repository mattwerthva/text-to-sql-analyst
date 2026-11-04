import { useRef, useState } from "react";
import { askStream, type AskEvent } from "./lib/api";

// First three are a deliberate progression of insight — spend → diagnose →
// act — so a first-time user can watch the analysis deepen. The rest show
// breadth (forecasting, maintenance burden, inspection compliance).
const EXAMPLES = [
  "How much did we spend on completed maintenance versus replacements in 2025?",
  "Which makes and models are failing early instead of lasting to end of life?",
  "Which assets are past their expected life with no scheduled replacement, and what's the projected CapEx by community?",
  "Project our replacement CapEx by year as each asset reaches the end of its expected life.",
  "Which make and model has cost us the most in completed maintenance?",
  "Show the latest inspection per asset in the NYC region.",
];

interface Result {
  sql?: string;
  rationale?: string;
  columns?: string[];
  rows?: unknown[][];
  narrative: string;
  error?: string;
}

export default function App() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [running, setRunning] = useState(false);
  const [showSql, setShowSql] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  async function run(q: string) {
    if (!q.trim() || running) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setQuestion(q);
    setShowSql(false);
    setRunning(true);
    setResult({ narrative: "" });

    try {
      await askStream(
        q,
        (event: AskEvent) => {
          setResult((prev) => {
            const next = prev ?? { narrative: "" };
            switch (event.type) {
              case "sql":
                return { ...next, sql: event.sql, rationale: event.rationale };
              case "rows":
                return { ...next, columns: event.columns, rows: event.rows };
              case "delta":
                return { ...next, narrative: next.narrative + event.text };
              case "error":
                return { ...next, error: event.message };
              case "done":
                return next;
            }
          });
        },
        controller.signal,
      );
    } catch (err) {
      if (!controller.signal.aborted) {
        setResult((prev) => ({ ...(prev ?? { narrative: "" }), error: String(err) }));
      }
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <div className="mx-auto max-w-4xl px-6 py-12">
        <header className="mb-8">
          <h1 className="text-3xl font-bold tracking-tight">
            Preventive-Maintenance &amp; CapEx Analyst
          </h1>
          <p className="mt-2 text-slate-600">
            Ask your portfolio anything. Claude writes the SQL, runs it read-only against
            108k inspection records, and explains the answer.
          </p>
        </header>

        {/* Question input */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(question);
          }}
          className="mb-4"
        >
          <div className="flex gap-2">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="e.g. Which buildings have the highest projected CapEx?"
              className="flex-1 rounded-lg border border-slate-300 bg-white px-4 py-3 text-sm shadow-sm outline-none focus:border-slate-500"
            />
            <button
              type="submit"
              disabled={running || !question.trim()}
              className="rounded-lg bg-slate-900 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-slate-700 disabled:opacity-40"
            >
              {running ? "Thinking…" : "Ask"}
            </button>
          </div>
        </form>

        {/* Example chips */}
        <div className="mb-8 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              onClick={() => run(ex)}
              disabled={running}
              className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-left text-xs text-slate-600 shadow-sm transition hover:border-slate-400 hover:text-slate-900 disabled:opacity-40"
            >
              {ex}
            </button>
          ))}
        </div>

        {result && (
          <div className="space-y-5">
            {result.error && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                {result.error}
              </div>
            )}

            {/* SQL — collapsed by default; transparency on demand */}
            {result.sql && (
              <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
                <button
                  onClick={() => setShowSql((s) => !s)}
                  className="flex w-full items-center justify-between px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  <span>
                    <span className="mr-2 text-slate-400">{showSql ? "▾" : "▸"}</span>
                    Generated SQL
                  </span>
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-xs font-normal text-emerald-700">
                    read-only · single SELECT · row-capped
                  </span>
                </button>
                {showSql && (
                  <div className="border-t border-slate-100">
                    {result.rationale && (
                      <p className="px-4 pt-3 text-xs italic text-slate-500">
                        {result.rationale}
                      </p>
                    )}
                    <pre className="overflow-x-auto px-4 py-3 text-xs leading-relaxed text-slate-800">
                      {result.sql}
                    </pre>
                  </div>
                )}
              </section>
            )}

            {/* Results grid */}
            {result.columns && (
              <ResultTable columns={result.columns} rows={result.rows ?? []} />
            )}

            {/* Streaming narrative */}
            {(result.narrative || (running && result.columns)) && (
              <section className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">
                  Analysis
                </h2>
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                  {result.narrative}
                  {running && <span className="ml-0.5 animate-pulse">▋</span>}
                </p>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// Format a cell: Postgres NUMERIC arrives as a high-precision string. Render
// numeric-looking values with thousands separators and up to 2 decimals.
function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "number") return value.toLocaleString();
  if (typeof value === "string" && /^-?\d+(\.\d+)?$/.test(value)) {
    const n = Number(value);
    if (Number.isFinite(n)) {
      return Number.isInteger(n)
        ? n.toLocaleString()
        : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }
  }
  return String(value);
}

function ResultTable({ columns, rows }: { columns: string[]; rows: unknown[][] }) {
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
        <h2 className="text-sm font-medium text-slate-700">Results</h2>
        <span className="text-xs text-slate-400">
          {rows.length} row{rows.length === 1 ? "" : "s"}
        </span>
      </div>
      {rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-slate-500">No rows returned.</p>
      ) : (
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-left text-sm">
            <thead className="sticky top-0 bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
              <tr>
                {columns.map((c) => (
                  <th key={c} className="whitespace-nowrap px-4 py-2 font-medium">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row, i) => (
                <tr key={i} className="hover:bg-slate-50">
                  {row.map((cell, j) => (
                    <td key={j} className="whitespace-nowrap px-4 py-2 text-slate-700">
                      {formatCell(cell)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
