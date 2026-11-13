import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Background, BackgroundVariant, Controls, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { ArrowUp, CircleHelp, Square, Wrench } from 'lucide-react';
import ApprovalBar from './ApprovalBar';
import FileLinks from './FileLinks';
import { buildFlow, edgeTypes, layoutFlow, nodeTypes, RefitOn } from './Graph';
import { applyLive, type LiveMessage, type LiveState } from './LiveFeed';
import HowItWorksDialog, { HowItWorksContent } from './HowItWorks';
import NodePanel from './NodePanel';
import PlanEditor from './PlanEditor';
import RoutingSelect, { modeSummary, modelLabel, parseRouting, routingKey } from './Routing';
import RunSwitcher, { type RunRef } from './RunSwitcher';
import ToolsDrawer from './ToolsDrawer';
import {
  cancelRun,
  decideRun,
  DELIVER_OPTIONS,
  deleteNode,
  editNode,
  fetchEstimate,
  fetchNode,
  fetchRun,
  fmtCost,
  fmtRange,
  listModels,
  listModes,
  listRuns,
  listSources,
  listTools,
  MAX_AGENTS_HINT,
  parseAgentTypes,
  setRunRouting,
  submitTask,
  type NodeEdit,
  type CostEstimate,
  type DeliverOption,
  type ModeInfo,
  type ModelInfo,
  type Routing,
  type Tier,
  type NodeDetail,
  type RunSummary,
  type ToolInfo,
} from './api';

const TERMINAL = new Set(['succeeded', 'failed', 'rejected', 'cancelled']);
const ACTIVE = new Set(['planning', 'awaiting_approval', 'queued', 'pending', 'running']);
const MIN_PANEL = 280;
const MAX_PANEL = 900;
const WIDE = '(min-width: 1100px)'; // below this the detail panel slides over the canvas instead

function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [query]);
  return match;
}

function initialRequireApproval(): boolean {
  try {
    return localStorage.getItem('requireApproval') !== 'false';
  } catch {
    return true;
  }
}

function initialPanelWidth(): number {
  const v = Number(localStorage.getItem('panelWidth'));
  return Number.isFinite(v) ? Math.min(MAX_PANEL, Math.max(MIN_PANEL, v)) : 400;
}


export default function App() {
  const [taskInput, setTaskInput] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunRef[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [summary, setSummary] = useState<RunSummary | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [panelWidth, setPanelWidth] = useState(initialPanelWidth);
  const [requireApproval, setRequireApproval] = useState(initialRequireApproval);
  const [deciding, setDeciding] = useState(false);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [modes, setModes] = useState<ModeInfo[]>([]);
  // "mode:<id>" or "model:<id>". The pre-routing 'model' key is ignored, so everyone starts on the default mode.
  const [routing, setRouting] = useState<Routing | null>(() => {
    try {
      return parseRouting(localStorage.getItem('routing'));
    } catch {
      return null;
    }
  });
  const [deliver, setDeliver] = useState<DeliverOption>(() => {
    try {
      const saved = localStorage.getItem('deliver');
      return DELIVER_OPTIONS.some((o) => o.id === saved) ? (saved as DeliverOption) : 'auto';
    } catch {
      return 'auto';
    }
  });
  const chooseDeliver = (d: DeliverOption) => {
    setDeliver(d);
    try {
      localStorage.setItem('deliver', d);
    } catch {
      // choice just won't be remembered
    }
  };
  // Optional planner hints, per session: '' = let the planner decide.
  const [agentCount, setAgentCount] = useState('');
  const [agentTypes, setAgentTypes] = useState('');
  const parsedTypes = useMemo(() => parseAgentTypes(agentTypes), [agentTypes]);
  const [sources, setSources] = useState<string[]>([]);
  const [focusFrom, setFocusFrom] = useState<string | null>(null);
  // Running agents' live output, fed by "live" stream events; transient, so it resets with the run.
  const [live, setLive] = useState<Record<string, LiveState>>({});
  const summaryRef = useRef<RunSummary | null>(null);
  const selectedRef = useRef<string | null>(null);
  const layoutRef = useRef<HTMLElement | null>(null);
  const draggingRef = useRef(false);
  selectedRef.current = selected;

  const refreshTools = () => {
    listTools()
      .then(setTools)
      .catch(() => {
        // editor then shows "No tools available"
      });
  };

  useEffect(refreshTools, []);

  useEffect(() => {
    listModels()
      .then(setModels)
      .catch(() => {
        // picker hidden; the server default is used
      });
    listModes()
      .then(setModes)
      .catch(() => {
        // picker hidden; the server default mode is used
      });
  }, []);
  // A remembered choice that is no longer offered falls back to the server's default mode.
  const offered = (r: Routing | null) =>
    r != null && ('mode' in r ? modes.some((m) => m.id === r.mode) : models.some((m) => m.id === r.model));
  const defaultMode = modes.find((m) => m.default);
  const chosenRouting: Routing | null = offered(routing) ? routing : defaultMode ? { mode: defaultMode.id } : null;
  const chooseRouting = (r: Routing) => {
    setRouting(r);
    try {
      localStorage.setItem('routing', routingKey(r));
    } catch {
      // choice just won't be remembered
    }
  };
  const routingTitle = (r: Routing | null): string | undefined => {
    if (!r) return undefined;
    if ('mode' in r) {
      const m = modes.find((x) => x.id === r.mode);
      return m ? modeSummary(m, models) : undefined;
    }
    return `${models.find((m) => m.id === r.model)?.note ?? ''} The planner and every agent use this one model.`.trim();
  };
  const runRoutingLabel = (mode: string | null | undefined, model: string) =>
    mode ? (modes.find((m) => m.id === mode)?.label ?? mode) : modelLabel(models, model);

  useEffect(() => {
    listSources()
      .then(setSources)
      .catch(() => {
        // no hint without source info
      });
  }, []);

  const noExternalEnabled = sources.length > 0 && !tools.some((t) => t.source !== 'builtin');

  // History chips show live status, so poll the run list (cheap) rather than stream every run.
  useEffect(() => {
    let alive = true;
    const load = () =>
      listRuns()
        .then((items) => {
          if (!alive) return;
          setRuns((prev) => {
            const fromServer: RunRef[] = items.map((i) => ({
              id: i.run_id,
              task: i.task,
              status: i.status,
              created_at: i.created_at,
              model: i.model,
              mode: i.mode,
              total_cost_usd: i.total_cost_usd,
            }));
            const known = new Set(fromServer.map((r) => r.id));
            return [...prev.filter((r) => !known.has(r.id)), ...fromServer];
          });
        })
        .catch(() => {
          // history is a convenience; the app works without it
        });
    void load();
    const timer = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setInterval> | undefined;
    const cleanup = () => {
      es?.close();
      if (timer) clearInterval(timer);
    };
    const refresh = async () => {
      try {
        const s = await fetchRun(runId);
        if (!alive) return;
        summaryRef.current = s;
        setSummary(s);
        const sel = selectedRef.current;
        if (sel) {
          try {
            setDetail(await fetchNode(runId, sel));
          } catch {
            // node detail may lag the summary; next event retries
          }
        }
        if (TERMINAL.has(s.status)) cleanup();
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err));
      }
    };
    void refresh();
    setLive({});
    // Live messages arrive several times a second; apply them in one state update per frame.
    let pending: LiveMessage[] = [];
    let frame = 0;
    const flushLive = () => {
      frame = 0;
      const batch = pending;
      pending = [];
      if (alive) setLive((prev) => batch.reduce((acc, m) => ({ ...acc, [m.node_id]: applyLive(acc[m.node_id], m) }), prev));
    };
    es = new EventSource(`/runs/${runId}/stream?after=0`);
    es.onmessage = () => void refresh();
    es.addEventListener('live', (e) => {
      try {
        pending.push(JSON.parse((e as MessageEvent).data) as LiveMessage);
      } catch {
        return; // a garbled live message just isn't shown
      }
      if (!frame) frame = requestAnimationFrame(flushLive);
    });
    es.onerror = () => {
      // The server ends each stream every few seconds; the browser then reconnects by itself
      // (resuming from the last event id). Only fall back to polling if it has given up.
      if (es?.readyState !== EventSource.CLOSED || timer) return;
      timer = setInterval(() => {
        const s = summaryRef.current;
        if (s && TERMINAL.has(s.status)) {
          cleanup();
          return;
        }
        void refresh();
      }, 2000);
    };
    return () => {
      alive = false;
      cancelAnimationFrame(frame);
      cleanup();
    };
  }, [runId]);

  // Tall or narrow screens stack the graph top to bottom; a wide left-to-right chain would shrink to unreadable.
  const compact = useMediaQuery('(max-width: 760px), (orientation: portrait)');
  // Layout only changes with the plan's shape; status and live updates just restyle nodes.
  const shapeKey = summary
    ? `${summary.nodes.map((n) => n.node_id).join(',')}|${summary.edges.map((e) => `${e.from}>${e.to}`).join(',')}`
    : '';
  const flowLayout = useMemo(
    () => (summary ? layoutFlow(summary, compact) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- summary is read only through shapeKey's parts
    [shapeKey, compact],
  );
  // Node cards show each running agent's phase; this changes only when a phase does, not on every token.
  const phaseKey = Object.entries(live)
    .map(([id, l]) => `${id}=${l.phase}`)
    .join('|');
  const phases = useMemo(
    () => Object.fromEntries(Object.entries(live).map(([id, l]) => [id, l.phase])),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- live is read only through phaseKey
    [phaseKey],
  );
  const flow = useMemo(
    () =>
      summary && flowLayout
        ? buildFlow(summary, flowLayout, selected, (id) => modelLabel(models, id), phases)
        : null,
    [summary, flowLayout, selected, models, phases],
  );

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!taskInput.trim() || submitting || parsedTypes.error) return;
    setSubmitting(true);
    setError(null);
    try {
      const { run_id } = await submitTask(taskInput.trim(), requireApproval, chosenRouting, deliver, {
        ...(agentCount ? { agents: Number(agentCount) } : {}),
        ...(parsedTypes.types.length ? { agent_types: parsedTypes.types } : {}),
      });
      setRuns((prev) => [
        {
          id: run_id,
          task: taskInput.trim(),
          status: 'planning',
          created_at: Date.now() / 1000,
          ...(chosenRouting && 'model' in chosenRouting ? { model: chosenRouting.model } : {}),
          ...(chosenRouting && 'mode' in chosenRouting ? { mode: chosenRouting.mode } : {}),
        },
        ...prev,
      ]);
      setRunId(run_id);
      setSummary(null);
      setSelected(null);
      setDetail(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const decide = async (decision: 'approve' | 'reject') => {
    if (!runId || deciding) return;
    setDeciding(true);
    setError(null);
    try {
      await decideRun(runId, decision);
      // the run's event stream picks up the new state and refreshes the graph
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeciding(false);
    }
  };

  const [stopping, setStopping] = useState(false);
  const wide = useMediaQuery(WIDE);

  // Cost forecast while a plan awaits approval; refetched whenever the plan's agents or tools change.
  const planKey = summary
    ? summary.nodes.map((n) => `${n.node_id}=${n.tools.join(',')}/${n.tier}/${n.model}`).join(';')
    : '';
  const awaiting = summary?.status === 'awaiting_approval';
  const [estimate, setEstimate] = useState<{ runId: string; data: CostEstimate } | null>(null);
  useEffect(() => {
    if (!runId || !awaiting) return;
    let alive = true;
    fetchEstimate(runId)
      .then((data) => alive && setEstimate({ runId, data }))
      .catch(() => alive && setEstimate(null));
    return () => {
      alive = false;
    };
  }, [runId, awaiting, planKey, summary?.model, summary?.mode]); // re-price when agents' models change
  const liveEstimate = estimate && estimate.runId === runId ? estimate.data : null;
  const stopRun = async () => {
    if (!runId || stopping) return;
    if (!window.confirm('Stop this run? Agents that already finished keep their results.')) return;
    setStopping(true);
    setError(null);
    try {
      await cancelRun(runId);
      // the event stream delivers the final state
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setStopping(false);
    }
  };

  const chipStatus = (r: RunRef) => (summary && r.id === summary.run_id ? summary.status : r.status);
  const runningCount = runs.filter((r) => ['pending', 'running'].includes(chipStatus(r) ?? '')).length;
  const queuedCount = runs.filter((r) => chipStatus(r) === 'queued').length;

  const closePanel = () => {
    setSelected(null);
    setDetail(null);
  };

  const saveNode = async (edit: NodeEdit) => {
    if (!runId || !detail) return;
    setError(null);
    try {
      await editNode(runId, detail.node_id, edit);
      setDetail(await fetchNode(runId, detail.node_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const removeNode = async () => {
    if (!runId || !detail) return;
    setError(null);
    try {
      await deleteNode(runId, detail.node_id);
      closePanel();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const openRun = (id: string) => {
    // Same id wouldn't re-trigger the loading effect, so clearing state would strand "Loading run…".
    if (id === runId) return;
    setRunId(id);
    setSummary(null);
    setSelected(null);
    setDetail(null);
    setError(null);
  };

  const panel =
    detail && summary?.status === 'awaiting_approval' ? (
      <PlanEditor
        key={detail.node_id}
        detail={detail}
        tools={tools}
        canDelete={summary.nodes.length > 1}
        estimate={liveEstimate?.nodes.find((n) => n.node_id === detail.node_id) ?? null}
        node={summary.nodes.find((n) => n.node_id === detail.node_id) ?? null}
        models={models}
        tierModel={(t: Tier) =>
          (summary.mode ? modes.find((m) => m.id === summary.mode)?.tiers[t] : undefined) ?? summary.model
        }
        onSave={saveNode}
        onDelete={removeNode}
        onClose={closePanel}
      />
    ) : (
      <NodePanel
        runId={runId}
        detail={detail}
        focusFrom={focusFrom}
        live={detail ? live[detail.node_id] : undefined}
        onClose={closePanel}
      />
    );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <svg className="brand-mark" viewBox="0 0 24 24" aria-hidden="true">
            <path d="M5 12h4M15 7.5 9 12l6 4.5" />
            <circle cx="5" cy="12" r="2.6" />
            <circle cx="17.5" cy="6.5" r="2.6" />
            <circle cx="17.5" cy="17.5" r="2.6" />
          </svg>
          <span>Dagwood</span>
        </div>
        <RunSwitcher runs={runs} currentId={runId} statusOf={chipStatus} onOpen={openRun} />
        <div className="topbar-end">
          {(runningCount > 0 || queuedCount > 0) && (
            <div className="activity" title="Your runs in progress across all tabs">
              <span className="dot running" />
              {runningCount} running{queuedCount > 0 ? ` · ${queuedCount} queued` : ''}
            </div>
          )}
          <button className="icon-button" onClick={() => setToolsOpen(true)} title="Tools agents may use">
            <Wrench size={16} />
            <span className="label">Tools</span>
            <span className="count">{tools.length}</span>
          </button>
          <button className="icon-button" onClick={() => setHelpOpen(true)} aria-label="How it works" title="How it works">
            <CircleHelp size={16} />
            <span className="label">How it works</span>
          </button>
        </div>
      </header>

      <form className="composer" onSubmit={onSubmit}>
        <div className="composer-box">
          <textarea
            value={taskInput}
            rows={1}
            onChange={(e) => setTaskInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                e.currentTarget.form?.requestSubmit();
              }
            }}
            placeholder="Describe a task for the agents…"
            aria-label="Task"
          />
          <button type="submit" className="run-button" disabled={submitting || !taskInput.trim() || !!parsedTypes.error}>
            <ArrowUp size={16} strokeWidth={2.5} />
            <span>{submitting ? 'Planning…' : 'Run'}</span>
          </button>
        </div>
        <div className="composer-options">
          {chosenRouting && (
            <label className="option">
              <span>Cost mode</span>
              <RoutingSelect
                modes={modes}
                models={models}
                value={chosenRouting}
                onChange={chooseRouting}
                title={`${routingTitle(chosenRouting) ?? ''} Applies to tasks you submit from now on; for a plan you are reviewing, use the selector in the approval bar.`}
              />
            </label>
          )}
          <label className="option">
            <span>Deliver as</span>
            <select
              value={deliver}
              onChange={(e) => chooseDeliver(e.target.value as DeliverOption)}
              title={DELIVER_OPTIONS.find((o) => o.id === deliver)?.hint}
            >
              {DELIVER_OPTIONS.map((o) => (
                <option key={o.id} value={o.id} title={o.hint}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <label className="option agents-count">
            <span>Agents</span>
            <select
              value={agentCount}
              onChange={(e) => setAgentCount(e.target.value)}
              title="Optional hint: how many agents the plan should have. The planner follows it where the task allows."
            >
              <option value="">Auto</option>
              {Array.from({ length: MAX_AGENTS_HINT }, (_, i) => (
                <option key={i + 1} value={i + 1}>
                  {i + 1}
                </option>
              ))}
            </select>
          </label>
          <label className="option agents-types">
            <span>Agent types</span>
            <input
              type="text"
              value={agentTypes}
              onChange={(e) => setAgentTypes(e.target.value)}
              placeholder="Any, or e.g. researcher, critic"
              aria-invalid={!!parsedTypes.error}
              title={
                parsedTypes.error ??
                'Optional hint: kinds of agents to use, separated by commas (researcher, coder, critic, or your own, like "fact checker"). They become the agents\' roles.'
              }
            />
          </label>
          <label className="switch">
            <input
              type="checkbox"
              role="switch"
              checked={requireApproval}
              onChange={(e) => {
                setRequireApproval(e.target.checked);
                try {
                  localStorage.setItem('requireApproval', String(e.target.checked));
                } catch {
                  // preference just won't persist
                }
              }}
            />
            <span className="track" aria-hidden="true" />
            Review plan first
          </label>
        </div>
      </form>
      {parsedTypes.error && <div className="banner error">{parsedTypes.error}</div>}
      {error && <div className="banner error">{error}</div>}
      {noExternalEnabled && (
        <div className="banner hint">
          <span>
            {sources.join(', ')} is connected, but no toolkits are enabled, so plans can only use the built-in tools.
          </span>
          <button className="link" onClick={() => setToolsOpen(true)}>
            Enable toolkits
          </button>
        </div>
      )}
      {helpOpen && <HowItWorksDialog onClose={() => setHelpOpen(false)} />}
      {toolsOpen && <ToolsDrawer enabled={tools} onClose={() => setToolsOpen(false)} onChanged={refreshTools} />}

      <main className="workspace" ref={layoutRef}>
        <section className="stage">
          {summary && (
            <div className="run-header">
              <div className="run-title">
                <span className={`pill ${summary.status}`}>{summary.status.replace('_', ' ')}</span>
                <h1 title={summary.task}>{summary.task}</h1>
              </div>
              <dl className="run-stats">
                <div title={`Includes planning: ${fmtCost(summary.planner.cost_usd)}. Costs are estimates from token counts.`}>
                  <dt>Cost</dt>
                  <dd className="cost">{fmtCost(summary.total_cost_usd)}</dd>
                </div>
                <div>
                  <dt>Tokens</dt>
                  <dd>{(summary.total_input_tokens + summary.total_output_tokens).toLocaleString()}</dd>
                </div>
                {summary.estimate && (
                  <div title="Forecast made when the plan was approved">
                    <dt>Forecast</dt>
                    <dd>{fmtRange(summary.estimate.typical_usd, summary.estimate.high_usd)}</dd>
                  </div>
                )}
                <div title={summary.mode ? `Cost mode; planned by ${summary.model}` : `Every agent on ${summary.model}`}>
                  <dt>{summary.mode ? 'Mode' : 'Model'}</dt>
                  <dd>{runRoutingLabel(summary.mode, summary.model)}</dd>
                </div>
                {(summary.agents || !!summary.agent_types?.length) && (
                  <div title="What you asked the planner for. A hint: the plan may differ where the task needs it.">
                    <dt>Asked for</dt>
                    <dd>
                      {[
                        summary.agents ? `${summary.agents} agent${summary.agents === 1 ? '' : 's'}` : null,
                        summary.agent_types?.join(', ') || null,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </dd>
                  </div>
                )}
                <div className="run-id">
                  <dt>Run</dt>
                  <dd className="mono">{summary.run_id}</dd>
                </div>
              </dl>
              {ACTIVE.has(summary.status) && (
                <button className="stop-button" onClick={() => void stopRun()} disabled={stopping}>
                  <Square size={11} fill="currentColor" />
                  {stopping ? 'Stopping…' : 'Stop'}
                </button>
              )}
            </div>
          )}
          {summary?.status === 'failed' && summary.error && (
            <div className="banner error">Run failed: {summary.error}</div>
          )}
          {summary?.status === 'queued' && (
            <div className="banner info">
              Queued{summary.queue_position ? ` (#${summary.queue_position} in line)` : ''}: it starts automatically
              when one of your running flows finishes.
            </div>
          )}
          {summary && summary.nodes.some((n) => (n.files ?? []).length > 0) && (
            <div className="banner deliverables">
              <span>Files from this run</span>
              <FileLinks runId={summary.run_id} files={summary.nodes.flatMap((n) => n.files ?? [])} />
            </div>
          )}
          {summary?.status === 'awaiting_approval' && (
            <ApprovalBar
              runId={summary.run_id}
              agentCount={summary.nodes.length}
              planKey={planKey}
              estimate={liveEstimate}
              modes={modes}
              models={models}
              routing={summary.mode ? { mode: summary.mode } : { model: summary.model }}
              routingTitle={routingTitle(summary.mode ? { mode: summary.mode } : { model: summary.model })}
              onRoutingChange={(r) => {
                setError(null);
                // the run's event stream delivers the change, which refetches the estimate
                setRunRouting(summary.run_id, r).catch((err) => setError(err instanceof Error ? err.message : String(err)));
              }}
              deciding={deciding}
              onDecide={(d) => void decide(d)}
            />
          )}
          <div className="canvas-wrap">
            {summary && summary.nodes.length === 0 ? (
              <div className="placeholder">
                {summary.status === 'planning' ? (
                  <>
                    <span className="planning-spinner" aria-hidden="true" />
                    Planning the agent flow…
                  </>
                ) : (
                  (summary.error ?? `Run ${summary.status} before a plan was made.`)
                )}
              </div>
            ) : summary && flow ? (
              <ReactFlow
                key={compact ? 'tb' : 'lr'} // remount so fitView frames the new layout
                nodes={flow.nodes}
                edges={flow.edges}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                colorMode="system"
                nodesDraggable={false}
                nodesConnectable={false}
                fitView
                fitViewOptions={{ maxZoom: 1, padding: 0.12 }} // small plans shouldn't be blown up to fill the canvas
                minZoom={0.2}
                onNodeClick={(_, node) => {
                  setFocusFrom(null);
                  setSelected(node.id);
                  if (runId) void fetchNode(runId, node.id).then(setDetail).catch(() => {});
                }}
                onEdgeClick={(_, edge) => {
                  // Show the receiving agent, with the data that travelled along this edge highlighted.
                  setFocusFrom(edge.source);
                  setSelected(edge.target);
                  if (runId) void fetchNode(runId, edge.target).then(setDetail).catch(() => {});
                }}
              >
                <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} />
                <Controls showInteractive={false} />
                <RefitOn trigger={`${wide && detail != null}:${summary.nodes.length}`} />
              </ReactFlow>
            ) : runId ? (
              <div className="placeholder">Loading run…</div>
            ) : (
              <div className="placeholder-explainer">
                <HowItWorksContent />
              </div>
            )}
          </div>
        </section>
        {wide && detail ? (
          <>
            <div
              className="resize-handle"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize detail panel"
              onPointerDown={(e) => {
                draggingRef.current = true;
                e.currentTarget.setPointerCapture(e.pointerId);
              }}
              onPointerMove={(e) => {
                if (!draggingRef.current || !layoutRef.current) return;
                const right = layoutRef.current.getBoundingClientRect().right;
                const w = Math.min(MAX_PANEL, Math.max(MIN_PANEL, Math.round(right - e.clientX)));
                setPanelWidth(w);
                localStorage.setItem('panelWidth', String(w));
              }}
              onPointerUp={() => {
                draggingRef.current = false;
              }}
              onPointerCancel={() => {
                draggingRef.current = false;
              }}
            />
            <div className="panel-wrap" style={{ width: panelWidth }}>
              {panel}
            </div>
          </>
        ) : (
          !wide &&
          detail && (
            <div className="sheet-backdrop" onClick={closePanel}>
              <div className="sheet" role="dialog" aria-label={detail.node_id} onClick={(e) => e.stopPropagation()}>
                {panel}
              </div>
            </div>
          )
        )}
      </main>
    </div>
  );
}
