import { X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Components } from 'react-markdown';
import { fmtCost, type NodeDetail } from './api';
import FileLinks from './FileLinks';
import LiveFeed, { type LiveState } from './LiveFeed';
import ToolCalls from './ToolCalls';

// Links in agent output open in a new tab so the run view stays put.
const MD_COMPONENTS: Components = {
  a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

interface Props {
  runId: string | null;
  detail: NodeDetail | null;
  focusFrom?: string | null; // upstream node whose input to highlight (set by clicking an edge)
  live?: LiveState; // what the agent is doing right now, while it runs
  onClose: () => void;
}

export default function NodePanel({ runId, detail, focusFrom = null, live, onClose }: Props) {
  if (!detail) {
    return <aside className="panel empty">Select an agent to see its inputs, tool calls, output and cost.</aside>;
  }
  const duration =
    detail.started_at != null && detail.ended_at != null
      ? `${(detail.ended_at - detail.started_at).toFixed(1)}s`
      : '—';
  const granted = detail.tools.length ? detail.tools.join(', ') : 'none';
  const running = detail.status === 'running';
  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-title mono">{detail.node_id}</div>
          <div className="panel-sub">
            <span>{detail.role}</span>
            <span className={`pill ${detail.status}`}>{detail.status.replace('_', ' ')}</span>
            {detail.tier && <span>{detail.tier}</span>}
            {detail.model && <span title={detail.model}>{detail.model.split('/').pop()}</span>}
          </div>
        </div>
        <button className="ghost" onClick={onClose} aria-label="Close panel">
          <X size={18} />
        </button>
      </div>

      <section>
        <h3>Goal</h3>
        <p>{detail.goal}</p>
      </section>

      {running && <LiveFeed live={live} components={MD_COMPONENTS} />}

      {runId && (detail.files ?? []).length > 0 && (
        <section>
          <h3>Files</h3>
          <FileLinks runId={runId} files={detail.files ?? []} />
        </section>
      )}

      {detail.instructions && (
        <section>
          <h3>Instructions</h3>
          <p>{detail.instructions}</p>
        </section>
      )}

      {detail.error && (
        <section className="error">
          <h3>Error</h3>
          <pre>{detail.error}</pre>
        </section>
      )}

      <section>
        <h3>Run stats</h3>
        <dl className="kv">
          <div>
            <dt>Duration</dt>
            <dd>{duration}</dd>
          </div>
          <div>
            <dt>Tokens in / out</dt>
            <dd>
              {detail.input_tokens.toLocaleString()} / {detail.output_tokens.toLocaleString()}
            </dd>
          </div>
          <div>
            <dt>Cost (est.)</dt>
            <dd>{fmtCost(detail.cost_usd)}</dd>
          </div>
          <div>
            <dt>Tools granted</dt>
            <dd>{granted}</dd>
          </div>
          <div>
            <dt>Tool calls</dt>
            <dd>
              {detail.tool_calls.filter((c) => c.kind !== 'discovery').length}
              {detail.tool_calls.some((c) => c.ok === false) && (
                <span className="failed-count">
                  {' '}
                  ({detail.tool_calls.filter((c) => c.ok === false).length} failed)
                </span>
              )}
              {detail.tool_calls.some((c) => c.empty) && (
                <span className="empty-count">
                  {' '}
                  ({detail.tool_calls.filter((c) => c.empty).length} returned no data)
                </span>
              )}
            </dd>
          </div>
        </dl>
      </section>

      {(detail.inputs ?? []).length > 0 && (
        <section>
          <h3>Inputs from upstream agents</h3>
          {detail.inputs.map((inp) => {
            const focused = inp.from_node === focusFrom;
            const clipped = inp.original_chars > inp.content.length;
            return (
              <details
                key={inp.from_node}
                className={focused ? 'input-record focused' : 'input-record'}
                open={focused || detail.inputs.length === 1}
                ref={(el) => {
                  if (el && focused) el.scrollIntoView({ block: 'nearest' });
                }}
              >
                <summary>
                  from <code>{inp.from_node}</code>{' '}
                  <span className="muted">
                    {clipped
                      ? `clipped to ${inp.content.length.toLocaleString()} of ${inp.original_chars.toLocaleString()} chars`
                      : `${inp.content.length.toLocaleString()} chars`}
                  </span>
                </summary>
                <div className="md scroll">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{inp.content || '(empty)'}</ReactMarkdown>
                </div>
              </details>
            );
          })}
        </section>
      )}

      {detail.thoughts.length > 0 && (
        <details className="thoughts">
          <summary>Thoughts ({detail.thoughts.length})</summary>
          {detail.thoughts.map((t, i) => (
            <pre key={i} className="scroll">
              {t}
            </pre>
          ))}
        </details>
      )}

      <ToolCalls calls={detail.tool_calls} />

      {!running && (
        <section>
          <h3>Output</h3>
          <div className="md scroll">
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>{detail.output || '—'}</ReactMarkdown>
          </div>
        </section>
      )}

      <section>
        <h3>Execution log</h3>
        {detail.logs.length ? (
          <ul className="logs">
            {detail.logs.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="muted">No log entries yet.</p>
        )}
      </section>

      <details>
        <summary>Input prompt</summary>
        <pre className="scroll">{detail.input || '—'}</pre>
      </details>
    </aside>
  );
}
