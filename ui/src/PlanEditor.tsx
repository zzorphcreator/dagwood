import { X } from 'lucide-react';
import { useState } from 'react';
import {
  DELIVERABLE_LABELS,
  fmtCost,
  TIERS,
  type Deliverable,
  type ModelInfo,
  type NodeDetail,
  type NodeEdit,
  type NodeEstimate,
  type NodeSummary,
  type Tier,
  type ToolInfo,
} from './api';
import { modelLabel } from './Routing';

const TIER_HINT: Record<Tier, string> = {
  light: 'simple extraction, formatting or lookups',
  standard: 'ordinary research or writing',
  heavy: 'short but hard reasoning; thinks harder (medium effort)',
};

interface Props {
  detail: NodeDetail;
  tools: ToolInfo[];
  canDelete: boolean;
  estimate: NodeEstimate | null; // forecast for this agent as currently planned
  node: NodeSummary | null; // current tier/model as the server resolves them
  models: ModelInfo[];
  tierModel: (tier: Tier) => string; // what a tier runs on under this run's mode
  onSave: (edit: NodeEdit) => Promise<void>;
  onDelete: () => Promise<void>;
  onClose: () => void;
}

// Mounted with key={node_id}, so drafts reset when a different node is selected.
export default function PlanEditor({
  detail,
  tools,
  canDelete,
  estimate,
  node,
  models,
  tierModel,
  onSave,
  onDelete,
  onClose,
}: Props) {
  const savedTier: Tier = node?.tier ?? 'standard';
  const savedPin = node?.model_pinned ? node.model : ''; // "" = follow the tier
  const [tier, setTier] = useState<Tier>(savedTier);
  const [pin, setPin] = useState(savedPin);
  const savedDeliverable: Deliverable | '' = node?.deliverable ?? '';
  const [deliverable, setDeliverable] = useState<Deliverable | ''>(savedDeliverable);
  const [goal, setGoal] = useState(detail.goal);
  const [instructions, setInstructions] = useState(detail.instructions);
  const [granted, setGranted] = useState<string[]>(detail.tools);
  const [busy, setBusy] = useState(false);

  const sameTools =
    granted.length === detail.tools.length && granted.every((t) => detail.tools.includes(t));
  const dirty =
    goal !== detail.goal ||
    instructions !== detail.instructions ||
    !sameTools ||
    tier !== savedTier ||
    deliverable !== savedDeliverable ||
    pin !== savedPin;

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  const toggle = (name: string) =>
    setGranted((prev) => (prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name]));

  return (
    <aside className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-title mono">{detail.node_id}</div>
          <div className="panel-sub">
            <span>{detail.role}</span>
            <span className="pill awaiting_approval">editing plan</span>
          </div>
          {estimate && (
            <div className="node-estimate" title="Updates after you save tool changes">
              Est. ~{fmtCost(estimate.typical_usd)} (up to {fmtCost(estimate.high_usd)}) on{' '}
              {modelLabel(models, estimate.model)} ·{' '}
              {estimate.kind === 'search' ? 'web search agents cost the most' : `${estimate.kind} agent`}
            </div>
          )}
        </div>
        <button className="ghost" onClick={onClose} aria-label="Close panel">
          <X size={18} />
        </button>
      </div>

      <section>
        <h3>
          <label htmlFor="edit-goal">Goal</label>
        </h3>
        <textarea id="edit-goal" className="edit" rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
      </section>

      <section>
        <h3>
          <label htmlFor="edit-instructions">Instructions</label>
        </h3>
        <textarea
          id="edit-instructions"
          className="edit"
          rows={5}
          value={instructions}
          placeholder="Optional extra guidance for this agent"
          onChange={(e) => setInstructions(e.target.value)}
        />
      </section>

      <section>
        <h3>Model</h3>
        <div className="model-edit">
          <label>
            Tier{' '}
            <select value={tier} onChange={(e) => setTier(e.target.value as Tier)}>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t} → {modelLabel(models, tierModel(t))}
                </option>
              ))}
            </select>
          </label>
          <label>
            Model{' '}
            <select value={pin} onChange={(e) => setPin(e.target.value)}>
              <option value="">Tier default ({modelLabel(models, tierModel(tier))})</option>
              {models.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.label} · ${m.input_per_1m}/${m.output_per_1m} per 1M{m.premium ? ' · premium' : ''}
                </option>
              ))}
            </select>
          </label>
          <p className="muted">
            {tier}: {TIER_HINT[tier]}.{' '}
            {(detail.tools.length > 0 || granted.length > 0) &&
              'Agents with tools re-read every result, so stronger models cost much more here.'}
          </p>
        </div>
      </section>

      <section>
        <h3>
          <label htmlFor="edit-deliverable">Output</label>
        </h3>
        <select id="edit-deliverable" value={deliverable} onChange={(e) => setDeliverable(e.target.value as Deliverable | '')}>
          <option value="">Text</option>
          {(Object.keys(DELIVERABLE_LABELS) as Deliverable[]).map((d) => (
            <option key={d} value={d}>
              File: {DELIVERABLE_LABELS[d]}
            </option>
          ))}
        </select>
        {deliverable && (
          <p className="muted">
            A publishing step turns its inputs into this file. It must be a final step (nothing depends on it) and
            have no tools.
          </p>
        )}
      </section>

      <section>
        <h3>Tools</h3>
        {tools.length ? (
          <ul className="tool-picker">
            {tools.map((t) => (
              <li key={t.id}>
                <label title={t.description}>
                  <input type="checkbox" checked={granted.includes(t.id)} onChange={() => toggle(t.id)} />
                  <span>
                    {t.source === 'builtin' ? <code>{t.name}</code> : <strong>{t.name}</strong>}{' '}
                    {t.source !== 'builtin' && <span className="tag">{t.source}</span>}{' '}
                    <span className="muted">{t.description}</span>
                  </span>
                </label>
              </li>
            ))}
            {granted
              .filter((id) => !tools.some((t) => t.id === id))
              .map((id) => (
                <li key={id}>
                  <label title="No longer enabled in the Tools catalog; uncheck to remove">
                    <input type="checkbox" checked onChange={() => toggle(id)} />
                    <code>{id}</code> <span className="tag auth">not enabled</span>
                  </label>
                </li>
              ))}
          </ul>
        ) : (
          <p className="muted">No tools available.</p>
        )}
      </section>

      <div className="edit-actions">
        <button
          disabled={!dirty || !goal.trim() || busy}
          onClick={() =>
            void run(() =>
              onSave({
                goal,
                instructions,
                tools: granted,
                ...(tier !== savedTier ? { tier } : {}),
                ...(pin !== savedPin ? { model: pin } : {}),
                ...(deliverable !== savedDeliverable ? { deliverable } : {}),
              }),
            )
          }
        >
          Save changes
        </button>
        <button
          className="danger"
          disabled={!canDelete || busy}
          title={canDelete ? 'Remove this agent; its children inherit its inputs' : 'A plan needs at least one agent'}
          onClick={() => {
            if (window.confirm(`Delete "${detail.node_id}" from the plan? Its children will take its inputs instead.`)) {
              void run(onDelete);
            }
          }}
        >
          Delete node
        </button>
      </div>
    </aside>
  );
}
