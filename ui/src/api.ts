export interface NodeSummary {
  node_id: string;
  role: string;
  goal: string;
  tools: string[];
  status: string;
  error: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  tier: Tier;
  model: string; // what it ran on, or what it would run on now if not started
  model_pinned: boolean; // a reviewer chose this agent's model instead of its tier's
  deliverable?: Deliverable | null; // set on a publishing step: the file it produces
  files?: FileInfo[]; // files it produced
}

/** File formats a publishing step can produce, and the task-level "deliver as" choice. */
export type Deliverable = 'pdf' | 'slides' | 'pptx';
export type DeliverOption = 'auto' | 'none' | Deliverable;
export const DELIVERABLE_LABELS: Record<Deliverable, string> = {
  pdf: 'PDF report',
  slides: 'Slides (web)',
  pptx: 'PowerPoint',
};
export const DELIVER_OPTIONS: { id: DeliverOption; label: string; hint: string }[] = [
  { id: 'auto', label: 'Auto', hint: 'A file only when the task asks for one; the planner picks the format' },
  { id: 'none', label: 'Text only', hint: 'Never produce a file' },
  { id: 'pdf', label: DELIVERABLE_LABELS.pdf, hint: 'End with a styled PDF report' },
  { id: 'slides', label: DELIVERABLE_LABELS.slides, hint: 'End with a modern web slide deck (charts when the data has numbers)' },
  { id: 'pptx', label: DELIVERABLE_LABELS.pptx, hint: 'End with a .pptx deck for PowerPoint, Keynote or Google Slides' },
];

export interface FileInfo {
  name: string;
  format: Deliverable;
  media_type: string;
  size: number;
}

export const fileUrl = (runId: string, name: string, download = false) =>
  `/runs/${runId}/files/${encodeURIComponent(name)}${download ? '?download=1' : ''}`;

/** The run's final answer as a plain PDF, rendered on request (no model call). */
export const exportUrl = (runId: string, download = false) => `/runs/${runId}/export.pdf${download ? '?download=1' : ''}`;

export type Tier = 'light' | 'standard' | 'heavy';
export const TIERS: Tier[] = ['light', 'standard', 'heavy'];

export interface RunEdge {
  from: string;
  to: string;
  chars: number | null; // size of the data passed along this edge; null until it flows
}

export interface InputRecord {
  from_node: string;
  content: string;
  original_chars: number;
}

/** After-run comparison of the split run against doing it all in one go. Time and context are
 * measured; cost is a guess (another model would use different token counts) and is worded as one. */
export interface SplitSummary {
  agents: number;
  time: { agent_seconds: number; wall_seconds: number };
  context: { largest_handoff_chars: number; output_chars: number };
  cost: {
    actual_usd: number;
    frontier_low_usd: number;
    frontier_high_usd: number;
    frontier_models: string[];
    prices_checked: string;
  } | null;
}

/** Headline figures of one run, for a split run and its single-agent baseline side by side. */
export interface RunBrief {
  run_id: string;
  status: string;
  agents: number;
  model: string;
  mode: string | null;
  total_cost_usd: number;
  seconds: number | null; // first agent start to last agent end; null until an agent has finished
  error: string | null;
}

export interface RunCompare {
  viewing: 'split' | 'single'; // which of the two this run is
  split: RunBrief;
  single: RunBrief;
}

export interface RunSummary {
  run_id: string;
  task: string;
  status: string;
  model: string; // the planner's model; every agent's when mode is null
  mode: string | null; // cost mode routing agents by tier; null = one model for all agents
  deliver?: DeliverOption;
  agents?: number | null; // the submitter's hint for how many agents to plan
  agent_types?: string[]; // ...and which kinds (used as roles)
  error: string | null; // run-level failure, e.g. the planner failed
  queue_position: number | null; // 1-based place in line when queued
  planner: { input_tokens: number; output_tokens: number; cost_usd: number };
  estimate: CostEstimate | null; // forecast snapshotted when the plan was approved
  split?: SplitSummary | null; // what splitting the task bought; set once a run with 2+ agents has ended
  compare?: RunCompare | null; // this run next to its single-agent baseline (or the split run it re-does)
  created_at: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cost_usd: number;
  nodes: NodeSummary[];
  edges: RunEdge[];
}

export interface ToolCallRecord {
  name: string;
  args: string;
  result: string | null;
  ok?: boolean | null; // absent on runs recorded before success tracking
  kind?: string; // "action" | "discovery"
  via?: string | null;
  empty?: boolean; // succeeded but found nothing
  note?: string | null; // the tool's own explanation
}

export interface NodeDetail {
  node_id: string;
  role: string;
  goal: string;
  status: string;
  input: string;
  output: string;
  instructions: string;
  tools: string[];
  tier?: string;
  model?: string | null; // set once the agent starts
  tool_calls: ToolCallRecord[];
  thoughts: string[];
  inputs: InputRecord[];
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  logs: string[];
  files?: FileInfo[];
  error: string | null;
  started_at: number | null;
  ended_at: number | null;
}

async function ok(res: Response): Promise<unknown> {
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json();
}

/** How a run picks agent models: a cost mode (route by tier) or one model for everything. */
export type Routing = { mode: string } | { model: string };

/** Optional planner hints: how many agents, and which kinds. The planner follows them where the task allows. */
export interface TeamHint {
  agents?: number;
  agent_types?: string[];
}
export const MAX_AGENTS_HINT = 12;
export const MAX_AGENT_TYPES = 6;
const AGENT_TYPE = /^[A-Za-z][A-Za-z0-9 _-]{0,29}$/;

/** Parse "researcher, fact checker" into types; `error` explains what the server would reject. */
export function parseAgentTypes(text: string): { types: string[]; error: string | null } {
  const seen = new Map<string, string>();
  for (const raw of text.split(',')) {
    const t = raw.trim();
    if (t && !seen.has(t.toLowerCase())) seen.set(t.toLowerCase(), t);
  }
  const types = [...seen.values()];
  const bad = types.find((t) => !AGENT_TYPE.test(t));
  const error = bad
    ? `"${bad.length > 24 ? `${bad.slice(0, 24)}…` : bad}" is not a valid agent type: use up to 30 letters, digits, spaces or hyphens, starting with a letter`
    : types.length > MAX_AGENT_TYPES
      ? `At most ${MAX_AGENT_TYPES} agent types`
      : null;
  return { types, error };
}

export async function submitTask(
  task: string,
  requireApproval: boolean,
  routing: Routing | null,
  deliver: DeliverOption = 'auto',
  team: TeamHint = {},
): Promise<{ run_id: string }> {
  const res = await fetch('/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ task, require_approval: requireApproval, deliver, ...team, ...(routing ?? {}) }),
  });
  return (await ok(res)) as { run_id: string };
}

/** Start the same task as one agent on the strongest listed model. The new run waits for approval. */
export async function startBaseline(runId: string): Promise<{ run_id: string }> {
  return (await ok(await fetch(`/runs/${runId}/baseline`, { method: 'POST' }))) as { run_id: string };
}

export async function decideRun(runId: string, decision: 'approve' | 'reject'): Promise<void> {
  await ok(await fetch(`/runs/${runId}/${decision}`, { method: 'POST' }));
}

export async function fetchRun(runId: string): Promise<RunSummary> {
  return (await ok(await fetch(`/runs/${runId}`))) as RunSummary;
}

export async function fetchNode(runId: string, nodeId: string): Promise<NodeDetail> {
  return (await ok(await fetch(`/runs/${runId}/nodes/${nodeId}`))) as NodeDetail;
}

export function fmtCost(usd: number): string {
  return usd < 0.01 ? `$${usd.toFixed(6)}` : `$${usd.toFixed(4)}`;
}

export interface RunListItem {
  run_id: string;
  task: string;
  status: string;
  created_at: number;
  model: string;
  mode: string | null;
  total_cost_usd: number;
}

export interface ModelInfo {
  id: string;
  label: string;
  input_per_1m: number;
  output_per_1m: number;
  note: string;
  premium: boolean;
  default: boolean;
}

export async function listModels(): Promise<ModelInfo[]> {
  return (await ok(await fetch('/models'))) as ModelInfo[];
}

export interface ModeInfo {
  id: string;
  label: string;
  note: string;
  planner: string;
  tiers: Record<Tier, string>;
  default: boolean;
}

export async function listModes(): Promise<ModeInfo[]> {
  return (await ok(await fetch('/modes'))) as ModeInfo[];
}

export async function listRuns(): Promise<RunListItem[]> {
  return (await ok(await fetch('/runs'))) as RunListItem[];
}

export interface ToolInfo {
  id: string; // "calc" for built-ins, "composio:github" for external toolkits
  name: string;
  description: string;
  source: string;
  needs_auth: boolean;
  logo: string | null;
}

export interface CatalogItem extends ToolInfo {
  enabled: boolean;
}

export interface ConnectionStatus {
  id: string;
  name: string;
  connected: boolean;
  needs_auth: boolean;
}

export interface NodeEdit {
  goal?: string;
  instructions?: string;
  tools?: string[];
  tier?: Tier;
  model?: string; // "" = back to the tier's model
  deliverable?: Deliverable | ''; // "" = not a publishing step
}

export async function listTools(): Promise<ToolInfo[]> {
  return (await ok(await fetch('/tools'))) as ToolInfo[];
}

export async function editNode(runId: string, nodeId: string, edit: NodeEdit): Promise<void> {
  await ok(
    await fetch(`/runs/${runId}/nodes/${nodeId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(edit),
    }),
  );
}

export async function deleteNode(runId: string, nodeId: string): Promise<void> {
  await ok(await fetch(`/runs/${runId}/nodes/${nodeId}`, { method: 'DELETE' }));
}

export async function listSources(): Promise<string[]> {
  return ((await ok(await fetch('/tools/sources'))) as { configured: string[] }).configured;
}

export async function browseCatalog(search: string): Promise<CatalogItem[]> {
  const params = new URLSearchParams({ source: 'composio', limit: '40' });
  if (search.trim()) params.set('search', search.trim());
  return (await ok(await fetch(`/tools/catalog?${params}`))) as CatalogItem[];
}

export async function enableToolkit(id: string): Promise<void> {
  await ok(
    await fetch('/tools/enabled', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id }),
    }),
  );
}

export async function disableToolkit(id: string): Promise<void> {
  await ok(await fetch(`/tools/enabled/${encodeURIComponent(id)}`, { method: 'DELETE' }));
}

export async function runConnections(runId: string): Promise<ConnectionStatus[]> {
  return (await ok(await fetch(`/runs/${runId}/connections`))) as ConnectionStatus[];
}

export async function connectLink(id: string): Promise<string> {
  const res = await fetch('/tools/connect', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id }),
  });
  return ((await ok(res)) as { redirect_url: string }).redirect_url;
}

export async function cancelRun(runId: string): Promise<void> {
  await ok(await fetch(`/runs/${runId}/cancel`, { method: 'POST' }));
}

export interface NodeEstimate {
  node_id: string;
  model: string;
  tier: Tier;
  kind: string; // "search" | "tools" | "plain"
  typical_input_tokens: number;
  high_input_tokens: number;
  typical_usd: number;
  high_usd: number;
}

export interface CostEstimate {
  nodes: NodeEstimate[];
  typical_usd: number;
  high_usd: number;
  based_on: Record<string, number>;
  min_samples: number;
  input_cap_per_agent: number;
  planner_cost_usd?: number;
  options: Record<string, OptionPrice>; // the plan priced under each routing, keyed like routingKey()
  comparisons: Comparison[]; // the whole plan on other providers' models; not runnable here
  prices_checked?: string; // when the model rates behind these figures were last checked
  comparison_prices_checked: string;
}

export interface Comparison extends OptionPrice {
  id: string;
  provider: string;
  label: string;
  note: string;
}

export interface OptionPrice {
  typical_usd: number;
  high_usd: number;
}

export async function fetchEstimate(runId: string): Promise<CostEstimate> {
  return (await ok(await fetch(`/runs/${runId}/estimate`))) as CostEstimate;
}

/** Short dollar amount for estimates: "$0.0045" under a cent, else "$0.23". */
export const fmtUsd = (v: number) => (v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(2)}`);

/** "$0.10–$0.23" style range for estimates. */
export function fmtRange(low: number, high: number): string {
  return `${fmtUsd(low)}–${fmtUsd(high)}`;
}

/** Change how the agents of a plan awaiting approval pick models (a mode, or one model for all). */
export async function setRunRouting(runId: string, routing: Routing): Promise<void> {
  await ok(
    await fetch(`/runs/${runId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(routing),
    }),
  );
}
