import { useCallback, useEffect, useState } from 'react';
import {
  connectLink,
  fmtCost,
  fmtUsd,
  type Comparison,
  runConnections,
  type ConnectionStatus,
  type CostEstimate,
  type ModeInfo,
  type ModelInfo,
  type Routing,
} from './api';
import RoutingSelect from './Routing';

interface Props {
  runId: string;
  agentCount: number;
  planKey: string; // changes whenever the plan's toolkits change, triggering a re-check
  estimate: CostEstimate | null;
  modes: ModeInfo[];
  models: ModelInfo[];
  routing: Routing; // how this plan's agents pick models
  routingTitle?: string;
  onRoutingChange: (r: Routing) => void;
  deciding: boolean;
  onDecide: (decision: 'approve' | 'reject') => void;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Other providers' models, grouped by provider, in a collapsed list under the estimate. */
function ProviderComparison({ rows, checked }: { rows: Comparison[]; checked: string }) {
  const providers = [...new Set(rows.map((r) => r.provider))];
  return (
    <details className="provider-compare">
      <summary>Compare with other providers</summary>
      <p className="muted">
        Estimate only; these models can't run here. Whole plan on one model, at the token counts of this server's
        past DeepSeek agents (their tokenizers and reasoning lengths differ, and prompt caching isn't counted).
        Prices as of {checked}.
      </p>
      <table>
        <tbody>
          {providers.map((p) =>
            rows
              .filter((r) => r.provider === p)
              .map((r, i) => (
                <tr key={r.id} title={r.note}>
                  <td className="muted">{i === 0 ? p : ''}</td>
                  <td>{r.label}</td>
                  <td>~{fmtUsd(r.typical_usd)}</td>
                  <td className="muted">up to {fmtUsd(r.high_usd)}</td>
                </tr>
              )),
          )}
        </tbody>
      </table>
    </details>
  );
}

function basis(e: CostEstimate): string {
  const parts = Object.entries(e.based_on).map(([kind, n]) =>
    n >= e.min_samples ? `${n} past ${kind} agents` : `defaults for ${kind} agents (only ${n} past)`,
  );
  return `Typical = median, high = worst 10% of similar past agents, capped at ${e.input_cap_per_agent.toLocaleString()} input tokens per agent (less on premium models, e.g. 100,000 on Kimi K3). Based on ${parts.join(', ')}.`;
}

export default function ApprovalBar({
  runId,
  agentCount,
  planKey,
  estimate,
  modes,
  models,
  routing,
  routingTitle,
  onRoutingChange,
  deciding,
  onDecide,
}: Props) {
  const [statuses, setStatuses] = useState<ConnectionStatus[] | null>(null);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  // State is only set once the request settles, so this is safe to call from an effect.
  const load = useCallback(
    () =>
      runConnections(runId).then(
        (found) => {
          setStatuses(found);
          setCheckError(null);
        },
        (err) => {
          setStatuses(null);
          setCheckError(message(err));
        },
      ),
    [runId],
  );

  useEffect(() => {
    void load();
  }, [load, planKey]); // planKey: re-check whenever the plan's toolkits change

  const recheck = async () => {
    setChecking(true);
    await load();
    setChecking(false);
  };

  const connect = async (id: string) => {
    // Open the tab synchronously so popup blockers allow it, then point it at the link.
    const tab = window.open('', '_blank');
    try {
      const url = await connectLink(id);
      if (tab) tab.location.assign(url);
      else window.location.assign(url);
    } catch (err) {
      tab?.close();
      setCheckError(message(err));
    }
  };

  // Only block on a known-missing connection; a failed check shouldn't strand the run.
  const missing = (statuses ?? []).filter((s) => !s.connected);

  return (
    <div className="approval">
      <div className="approval-text">
        <div className="approval-head">
          <strong>Plan ready: {agentCount} agents</strong>
          <span className="muted">Select an agent to change its goal, tools or model, or delete it. Nothing runs until you approve.</span>
        </div>
        <div className="approval-controls">
          {modes.length > 0 && (
            <label className="option">
              <span>Run agents with</span>
              <RoutingSelect
                modes={modes}
                models={models}
                value={routing}
                onChange={onRoutingChange}
                disabled={deciding}
                title={routingTitle}
                prices={estimate?.options}
              />
            </label>
          )}
          {estimate && (
            <div className="estimate-line" title={basis(estimate)}>
              <span className="estimate-figure">~{fmtCost(estimate.typical_usd)}</span>
              <span className="muted">
                up to {fmtCost(estimate.high_usd)}
                {estimate.planner_cost_usd != null && ` · planning used ${fmtCost(estimate.planner_cost_usd)}`}
              </span>
            </div>
          )}
        </div>
        {estimate && estimate.comparisons?.length > 0 && (
          <ProviderComparison rows={estimate.comparisons} checked={estimate.comparison_prices_checked} />
        )}
        {missing.length > 0 && (
          <ul className="connect-list">
            {missing.map((s) => (
              <li key={s.id}>
                <strong>{s.name}</strong> needs a connected account.{' '}
                <button className="link" onClick={() => void connect(s.id)}>
                  Connect
                </button>
              </li>
            ))}
            <li>
              <button className="link" onClick={() => void recheck()} disabled={checking}>
                {checking ? 'Checking…' : 'Re-check connections'}
              </button>
            </li>
          </ul>
        )}
        {checkError && <div className="muted">Couldn't check connections: {checkError}</div>}
      </div>
      <div className="approval-actions">
        <button
          className="approve"
          onClick={() => onDecide('approve')}
          disabled={deciding || missing.length > 0}
          title={missing.length ? 'Connect the accounts above first' : undefined}
        >
          Approve &amp; run
        </button>
        <button className="reject" onClick={() => onDecide('reject')} disabled={deciding}>
          Reject
        </button>
      </div>
    </div>
  );
}
