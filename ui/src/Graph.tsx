import { Graph, layout as dagreLayout } from '@dagrejs/dagre';
import {
  BaseEdge,
  Handle,
  MarkerType,
  Position,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeHandle,
  type NodeProps,
  useReactFlow,
} from '@xyflow/react';
import { useEffect } from 'react';
import { Ban, Check, Circle, FileText, LoaderCircle, Pin, X } from 'lucide-react';
import { DELIVERABLE_LABELS, type NodeSummary, type RunSummary } from './api';

export const NODE_W = 232;
export const NODE_H = 58; // two lines: id + status icon, then tier · model
const COMPACT_W = 176; // narrow screens stack the graph top to bottom with slimmer cards
const HANDLE = 6;

/** Wide screens flow left to right (inputs enter on the left); compact ones top to bottom. Handle x/y are top-left corners. */
function nodeHandles(w: number, vertical: boolean): NodeHandle[] {
  const h = HANDLE / 2;
  return vertical
    ? [
        { type: 'target', position: Position.Top, x: w / 2 - h, y: -h, width: HANDLE, height: HANDLE },
        { type: 'source', position: Position.Bottom, x: w / 2 - h, y: NODE_H - h, width: HANDLE, height: HANDLE },
      ]
    : [
        { type: 'target', position: Position.Left, x: -h, y: NODE_H / 2 - h, width: HANDLE, height: HANDLE },
        { type: 'source', position: Position.Right, x: w - h, y: NODE_H / 2 - h, width: HANDLE, height: HANDLE },
      ];
}

interface Point {
  x: number;
  y: number;
}

export type AgentData = { node: NodeSummary; modelName: string; selected: boolean; vertical: boolean; phase?: string };
export type AgentNodeType = Node<AgentData, 'agent'>;
export type RoutedData = { points: Point[] };
export type RoutedEdgeType = Edge<RoutedData, 'routed'>;

/** Dagre positions (top-left corners) plus each edge's bend points, so edges that skip a column route around nodes. */
function layoutGraph(summary: RunSummary, w: number, vertical: boolean) {
  const g = new Graph({ multigraph: false });
  g.setGraph(
    vertical
      ? { rankdir: 'TB', nodesep: 16, ranksep: 56, edgesep: 12, marginx: 12, marginy: 12 }
      : { rankdir: 'LR', nodesep: 36, ranksep: 88, edgesep: 24, marginx: 24, marginy: 24 },
  );
  g.setDefaultEdgeLabel(() => ({}));
  summary.nodes.forEach((n) => g.setNode(n.node_id, { width: w, height: NODE_H }));
  summary.edges.forEach((e) => g.setEdge(e.from, e.to));
  dagreLayout(g);
  const pos = new Map<string, Point>();
  summary.nodes.forEach((n) => {
    const p = g.node(n.node_id) as Point; // dagre gives centres
    pos.set(n.node_id, { x: p.x - w / 2, y: p.y - NODE_H / 2 });
  });
  const bends = new Map<string, Point[]>();
  summary.edges.forEach((e) => {
    const pts = ((g.edge(e.from, e.to) as { points?: Point[] } | undefined)?.points ?? []).slice(1, -1);
    bends.set(`${e.from}->${e.to}`, pts);
  });
  return { pos, bends };
}

function fmtChars(n: number): string {
  return n < 1000 ? `${n} chars` : `${(n / 1000).toFixed(1)}k chars`;
}

export type FlowLayout = ReturnType<typeof layoutGraph> & { w: number; vertical: boolean };

/** Positions depend only on the plan's shape, so callers memoise this apart from status and live updates. */
export function layoutFlow(summary: RunSummary, vertical = false): FlowLayout {
  const w = vertical ? COMPACT_W : NODE_W;
  return { ...layoutGraph(summary, w, vertical), w, vertical };
}

export function buildFlow(
  summary: RunSummary,
  layout: FlowLayout,
  selected: string | null,
  modelName: (id: string) => string,
  phases: Record<string, string> = {},
): { nodes: AgentNodeType[]; edges: RoutedEdgeType[] } {
  const { pos, bends, w, vertical } = layout;
  const handles = nodeHandles(w, vertical);
  const nodes: AgentNodeType[] = summary.nodes.map((n) => ({
    id: n.node_id,
    type: 'agent',
    position: pos.get(n.node_id) ?? { x: 0, y: 0 },
    // Fixed size and explicit handles: React Flow then never has to measure a node before
    // showing it or drawing its edges. Relying on measurement raced with our frequent
    // refreshes and could leave nodes hidden or edges undrawn. `measured` too, or React Flow
    // counts the rebuilt nodes as unmeasured and silently skips fitView.
    width: w,
    height: NODE_H,
    measured: { width: w, height: NODE_H },
    handles,
    data: {
      node: n,
      modelName: modelName(n.model),
      selected: selected === n.node_id,
      vertical,
      phase: n.status === 'running' ? phases[n.node_id] : undefined,
    },
  }));
  const status = new Map(summary.nodes.map((n) => [n.node_id, n.status]));
  const edges: RoutedEdgeType[] = summary.edges.map((e) => {
    const id = `${e.from}->${e.to}`;
    const live = status.get(e.to) === 'running';
    return {
      id,
      type: 'routed',
      source: e.from,
      target: e.to,
      animated: live,
      className: [live ? 'live' : '', e.chars == null ? '' : 'has-data'].join(' ').trim(),
      label: e.chars == null ? undefined : fmtChars(e.chars),
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'var(--edge)' },
      data: { points: bends.get(id) ?? [] },
    };
  });
  return { nodes, edges };
}

const STATUS_ICON: Record<string, typeof Check> = {
  succeeded: Check,
  failed: X,
  running: LoaderCircle,
  skipped: Ban,
  cancelled: Ban,
};

export function AgentNode({ data }: NodeProps<AgentNodeType>) {
  const n = data.node;
  const Icon = STATUS_ICON[n.status] ?? Circle;
  return (
    <div className={`agent-node s-${n.status}${data.selected ? ' selected' : ''}`} title={`${n.node_id}: ${n.goal}`}>
      <Handle type="target" position={data.vertical ? Position.Top : Position.Left} isConnectable={false} />
      <div className="agent-top">
        <span className="agent-id">{n.node_id}</span>
        <span className="agent-status" aria-label={n.status}>
          <Icon size={14} strokeWidth={2.5} />
        </span>
      </div>
      {data.phase ? (
        <div className="agent-sub agent-phase">{data.phase}…</div>
      ) : (
      <div className={`agent-sub tier-${n.tier}`}>
        {n.deliverable ? (
          <>
            <FileText size={12} /> {DELIVERABLE_LABELS[n.deliverable]}
          </>
        ) : (
          <span className="tier">{n.tier}</span>
        )}
        {n.model_pinned && <Pin size={11} aria-label="model pinned" />}
        <span className="agent-model">{data.modelName}</span>
      </div>
      )}
      <Handle type="source" position={data.vertical ? Position.Bottom : Position.Right} isConnectable={false} />
    </div>
  );
}

/** A smooth path from source to target through dagre's bend points (corners rounded with quadratic curves). */
function routedPath(pts: Point[]): { path: string; mid: Point } {
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const p = pts[i];
    const next = pts[i + 1];
    const end = { x: (p.x + next.x) / 2, y: (p.y + next.y) / 2 };
    d += ` Q ${p.x} ${p.y} ${end.x} ${end.y}`;
  }
  const last = pts[pts.length - 1];
  d += ` L ${last.x} ${last.y}`;
  // label at the middle bend (or the midpoint of a straight edge)
  const mid =
    pts.length > 2
      ? pts[Math.floor(pts.length / 2)]
      : { x: (pts[0].x + last.x) / 2, y: (pts[0].y + last.y) / 2 };
  return { path: d, mid };
}

export function RoutedEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  data,
  label,
  markerEnd,
  style,
}: EdgeProps<RoutedEdgeType>) {
  // Short stubs out of and into the handles keep the arrow pointing into the node.
  const vertical = sourcePosition === Position.Bottom;
  const pts = [
    { x: sourceX, y: sourceY },
    vertical ? { x: sourceX, y: sourceY + 14 } : { x: sourceX + 16, y: sourceY },
    ...(data?.points ?? []),
    vertical ? { x: targetX, y: targetY - 14 } : { x: targetX - 16, y: targetY },
    { x: targetX, y: targetY },
  ];
  const { path, mid } = routedPath(pts);
  return (
    <BaseEdge
      path={path}
      markerEnd={markerEnd}
      style={style}
      interactionWidth={18}
      label={label}
      labelX={mid.x}
      labelY={mid.y}
      labelShowBg
      labelBgPadding={[6, 3]}
      labelBgBorderRadius={9}
    />
  );
}

/** Re-frames the graph when `trigger` changes, e.g. when the side panel opens and the canvas narrows. */
export function RefitOn({ trigger }: { trigger: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    // React Flow learns the canvas's new size from a ResizeObserver, which fires after layout; wait for it.
    const id = setTimeout(() => void fitView({ maxZoom: 1, padding: 0.12, duration: 200 }), 80);
    return () => clearTimeout(id);
  }, [trigger, fitView]);
  return null;
}

export const nodeTypes = { agent: AgentNode };
export const edgeTypes = { routed: RoutedEdge };
