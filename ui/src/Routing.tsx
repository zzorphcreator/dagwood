import { fmtUsd, type ModeInfo, type ModelInfo, type OptionPrice, type Routing } from './api';

/** Select value encoding: "mode:<id>" or "model:<id>". */
export const routingKey = (r: Routing): string => ('mode' in r ? `mode:${r.mode}` : `model:${r.model}`);

export function parseRouting(key: string | null): Routing | null {
  if (key?.startsWith('mode:')) return { mode: key.slice(5) };
  if (key?.startsWith('model:')) return { model: key.slice(6) };
  return null;
}

export const modelLabel = (models: ModelInfo[], id: string) =>
  models.find((m) => m.id === id)?.label ?? id.split('/').pop() ?? id;

/** One-line description of what a mode runs each tier on. */
export function modeSummary(mode: ModeInfo, models: ModelInfo[]): string {
  const t = mode.tiers;
  return `${mode.note} Light → ${modelLabel(models, t.light)}, standard → ${modelLabel(models, t.standard)}, heavy → ${modelLabel(models, t.heavy)}.`;
}

interface Props {
  modes: ModeInfo[];
  models: ModelInfo[];
  value: Routing;
  onChange: (r: Routing) => void;
  className?: string;
  disabled?: boolean;
  title?: string;
  prices?: Record<string, OptionPrice>; // the current plan's estimate under each option, when there is a plan
}

const fmtPrice = (p: OptionPrice) => `~${fmtUsd(p.typical_usd)} (up to ${fmtUsd(p.high_usd)})`;
const fmtRates = (m: ModelInfo) => `$${m.input_per_1m}/$${m.output_per_1m} per 1M`;

/** Cost modes first (agents routed by tier), then single models as an escape hatch. */
export default function RoutingSelect({ modes, models, value, onChange, className, disabled, title, prices }: Props) {
  return (
    <select
      className={className}
      value={routingKey(value)}
      disabled={disabled}
      title={title}
      onChange={(e) => {
        const r = parseRouting(e.target.value);
        if (r) onChange(r);
      }}
    >
      <optgroup label="Cost mode (model per agent tier)">
        {modes.map((m) => (
          <option key={m.id} value={`mode:${m.id}`}>
            {m.label}
            {m.default ? ' (default)' : ''}
            {prices?.[`mode:${m.id}`] ? ` · ${fmtPrice(prices[`mode:${m.id}`])}` : ''}
          </option>
        ))}
      </optgroup>
      <optgroup label="One model for all agents">
        {models.map((m) => (
          <option key={m.id} value={`model:${m.id}`} title={fmtRates(m)}>
            {m.label} · {prices?.[`model:${m.id}`] ? fmtPrice(prices[`model:${m.id}`]) : fmtRates(m)}
            {m.premium ? ' · premium' : ''}
          </option>
        ))}
      </optgroup>
    </select>
  );
}
