// After a run: what splitting the task into agents bought, next to doing it all in one go.
// Time and context are measured from the run. The flagship price is a guess, and the wording
// here keeps it one: nobody ran that model, and it would not have used the same tokens.
// The single-agent baseline is the measured version of that comparison: one real run each.
import { useState } from 'react';
import { ChevronRight } from 'lucide-react';
import { fmtUsd, type RunBrief, type RunCompare, type SplitSummary } from './api';

function fmtDuration(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1)}s`;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)}m ${whole % 60}s`;
}

const fmtChars = (n: number) => (n < 1000 ? `${n}` : `${(n / 1000).toFixed(1)}k`);

/** One significant figure, so a guess doesn't read like a measurement: "$0.02", "$0.1", "$3". */
function fmtRough(usd: number): string {
  if (usd < 0.0001) return 'under $0.0001';
  return `$${Number(usd.toPrecision(1))}`;
}

const DONE = new Set(['succeeded', 'failed', 'cancelled', 'rejected']);
const briefFigures = (b: RunBrief) =>
  `${fmtUsd(b.total_cost_usd)}${b.seconds != null ? ` in ${fmtDuration(b.seconds)}` : ''}`;

/** Two measured runs side by side. One run each is an anecdote, and the text says so. */
function CompareTable({ compare, modelName }: { compare: RunCompare; modelName: (id: string) => string }) {
  const row = (label: string, b: RunBrief) => (
    <tr>
      <th scope="row">{label}</th>
      <td>{b.status === 'succeeded' ? 'finished' : b.status.replace('_', ' ')}</td>
      <td>{fmtUsd(b.total_cost_usd)}</td>
      <td>{b.seconds != null ? fmtDuration(b.seconds) : '–'}</td>
    </tr>
  );
  return (
    <table className="compare-table">
      <thead>
        <tr>
          <th />
          <th>Result</th>
          <th>Cost</th>
          <th>Time</th>
        </tr>
      </thead>
      <tbody>
        {row(`${compare.split.agents} agents`, compare.split)}
        {row(`1 agent, ${modelName(compare.single.model)}`, compare.single)}
      </tbody>
    </table>
  );
}

const CAVEAT =
  'One run each, so this is an anecdote, not a benchmark. The single agent works under the same per-agent limits on tool calls and input tokens as every other agent, so a search-heavy task can stop it early. Cost and time are only half of it: open both runs and compare the answers.';

interface Props {
  split: SplitSummary;
  compare: RunCompare | null;
  modelName: (id: string) => string;
  onBaseline: () => Promise<void>; // start the single-agent run and open it
  onOpen: (runId: string) => void;
}

export default function SplitStrip({ split, compare, modelName, onBaseline, onOpen }: Props) {
  const [starting, setStarting] = useState(false);
  const { time, context, cost } = split;
  const overlapped = time.wall_seconds < time.agent_seconds * 0.95;
  const roughRange = cost
    ? fmtRough(cost.frontier_low_usd) === fmtRough(cost.frontier_high_usd)
      ? `around ${fmtRough(cost.frontier_low_usd)}`
      : `somewhere around ${fmtRough(cost.frontier_low_usd)} to ${fmtRough(cost.frontier_high_usd)}`
    : null;
  const single = compare?.single;
  const singleDone = !!single && DONE.has(single.status);

  const start = async () => {
    setStarting(true);
    try {
      await onBaseline();
    } finally {
      setStarting(false);
    }
  };

  return (
    <details className="split-strip">
      <summary>
        <ChevronRight className="split-chevron" size={14} aria-hidden="true" />
        <span className="split-title">What the split bought</span>
        <span className="split-facts">
          <span>
            {fmtDuration(time.agent_seconds)} of agent work in {fmtDuration(time.wall_seconds)}
          </span>
          <span>
            largest handoff {fmtChars(context.largest_handoff_chars)} of {fmtChars(context.output_chars)} chars
          </span>
          {single && singleDone ? (
            <span>
              as one agent: {single.status === 'succeeded' ? briefFigures(single) : single.status}
            </span>
          ) : (
            cost &&
            roughRange && (
              <span>
                ~{fmtUsd(cost.actual_usd)} here; a flagship model might be {roughRange}
              </span>
            )
          )}
        </span>
      </summary>
      <dl>
        <div>
          <dt>Time</dt>
          <dd>
            The {split.agents} agents worked for {fmtDuration(time.agent_seconds)} in total, and the run took{' '}
            {fmtDuration(time.wall_seconds)}.{' '}
            {overlapped
              ? 'The difference is agents running side by side.'
              : 'Nothing overlapped in this plan, so splitting saved no time here.'}
          </dd>
        </div>
        <div>
          <dt>Context</dt>
          <dd>
            Agents wrote {fmtChars(context.output_chars)} characters between them. The most any one agent was handed
            by the others was {fmtChars(context.largest_handoff_chars)}. A single agent doing the whole task carries
            everything it has read and written so far into every later step.
          </dd>
        </div>
        {cost && roughRange && (
          <div>
            <dt>Cost</dt>
            <dd>
              This run cost about {fmtUsd(cost.actual_usd)}. The same amount of text at flagship-model prices might
              come to {roughRange}, but that is a guess, not a measurement: nobody ran that model, a different
              model would read and write a different amount, and one agent doing everything would not make the same
              calls. It could land well outside that range.{' '}
              <span className="muted">
                Range across {cost.frontier_models.join(', ')}; list prices checked {cost.prices_checked}.
              </span>
            </dd>
          </div>
        )}
        <div>
          <dt>Against one agent</dt>
          {compare && single ? (
            <dd>
              <CompareTable compare={compare} modelName={modelName} />
              {single.status === 'awaiting_approval' && 'The single-agent run is waiting for your approval. '}
              {single.error && singleDone && <>It stopped with: {single.error}. </>}
              <button className="link" onClick={() => onOpen(single.run_id)}>
                Open the single-agent run
              </button>
              <p className="muted compare-caveat">{CAVEAT}</p>
            </dd>
          ) : (
            <dd>
              To measure it, not guess: run the same task as one agent holding every tool this plan used, on the
              strongest model in the list. It opens as a new run that waits for your approval and shows an estimate
              first, because that model costs more.{' '}
              <button className="link" onClick={() => void start()} disabled={starting}>
                {starting ? 'Starting…' : 'Run it as one agent'}
              </button>
            </dd>
          )}
        </div>
      </dl>
    </details>
  );
}

/** Shown on the single-agent run itself: what it is, and the split run it is measured against. */
export function BaselineBanner({ compare, modelName, onOpen }: Pick<Props, 'modelName' | 'onOpen'> & { compare: RunCompare }) {
  const done = DONE.has(compare.single.status);
  return (
    <div className="split-strip baseline">
      <p>
        <span className="split-title">Single-agent baseline.</span> The same task as a {compare.split.agents}-agent
        run, done by one agent with all of that plan’s tools.{' '}
        <button className="link" onClick={() => onOpen(compare.split.run_id)}>
          Open the {compare.split.agents}-agent run
        </button>
      </p>
      {done && (
        <>
          <CompareTable compare={compare} modelName={modelName} />
          <p className="muted compare-caveat">{CAVEAT}</p>
        </>
      )}
    </div>
  );
}
