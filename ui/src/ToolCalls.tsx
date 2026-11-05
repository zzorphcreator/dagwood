import type { ToolCallRecord } from './api';

function parse(text: string | null): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function short(value: unknown): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value);
  return s.length > 80 ? `${s.slice(0, 80)}…` : s;
}

function Args({ raw }: { raw: string }) {
  const parsed = parse(raw);
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const entries = Object.entries(parsed as Record<string, unknown>);
    if (!entries.length) return <span className="muted">no arguments</span>;
    return (
      <span className="tool-args">
        {entries.map(([k, v]) => (
          <span key={k} className="arg">
            <span className="arg-key">{k}:</span> {short(v)}
          </span>
        ))}
      </span>
    );
  }
  return <code>{raw || '{}'}</code>;
}

function pretty(text: string): string {
  const parsed = parse(text);
  return parsed === undefined ? text : JSON.stringify(parsed, null, 2);
}

// What a lookup was for, e.g. COMPOSIO_SEARCH_TOOLS {"queries":[{"use_case":"get AERO price"}]}.
function lookupPurpose(call: ToolCallRecord): string {
  const parsed = parse(call.args) as { queries?: { use_case?: string }[]; tool_slugs?: string[] } | undefined;
  const uses = parsed?.queries?.map((q) => q.use_case).filter(Boolean);
  if (uses?.length) return uses.join('; ');
  if (parsed?.tool_slugs?.length) return `schemas for ${parsed.tool_slugs.join(', ')}`;
  return call.name;
}

function StatusIcon({ ok, empty }: { ok?: boolean | null; empty?: boolean }) {
  if (ok === undefined) return null; // run recorded before success tracking existed
  if (ok === true && empty) return <span className="call-status empty" title="Ran, but found no data">⚠</span>;
  if (ok === true) return <span className="call-status ok" title="Succeeded">✓</span>;
  if (ok === false) return <span className="call-status failed" title="Failed">✗</span>;
  return <span className="call-status pending" title="No result">…</span>;
}

export default function ToolCalls({ calls }: { calls: ToolCallRecord[] }) {
  const actions = calls.filter((c) => c.kind !== 'discovery');
  const lookups = calls.filter((c) => c.kind === 'discovery');
  if (!calls.length) return null;
  return (
    <section>
      <h3>Tool calls</h3>
      {actions.length > 0 ? (
        <ol className="tool-calls">
          {actions.map((c, i) => (
            <li key={i} className={c.ok === false ? 'call failed' : c.empty ? 'call empty' : 'call'}>
              <div className="call-head">
                <StatusIcon ok={c.ok} empty={c.empty} /> <span className="tool-name">{c.name}</span>
                {c.empty && <span className="tag auth">no data</span>}
              </div>
              <Args raw={c.args} />
              {c.note && <div className="call-note">{c.note}</div>}
              {c.ok === false ? (
                <pre className="tool-error">{c.result ?? 'failed'}</pre>
              ) : c.result != null ? (
                <details className="tool-result-details">
                  <summary>Result ({c.result.length.toLocaleString()} chars)</summary>
                  <pre className="scroll tool-result">{pretty(c.result)}</pre>
                </details>
              ) : (
                <div className="muted">no result</div>
              )}
              {c.via && <div className="muted via">run via {c.via}</div>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">No actions run, only tool lookups.</p>
      )}
      {lookups.length > 0 && (
        <details className="lookups">
          <summary>
            Tool lookups ({lookups.length}): the agent searching for which tools to use
          </summary>
          <ul>
            {lookups.map((c, i) => (
              <li key={i}>
                <StatusIcon ok={c.ok} /> {lookupPurpose(c)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
