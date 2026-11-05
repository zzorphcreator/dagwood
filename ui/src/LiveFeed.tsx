import { useLayoutEffect, useRef } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Check, LoaderCircle, X } from 'lucide-react';

export interface LiveSegment {
  kind: 'thinking' | 'text' | 'tool';
  text: string;
  meta: { args?: string; status?: 'running' | 'ok' | 'failed'; kind?: string };
}

export interface LiveState {
  phase: string;
  segments: LiveSegment[];
}

/** One "live" SSE message: the agent's phase plus segment ops (whole segment at 0 with kind, or appended text). */
export interface LiveMessage {
  node_id: string;
  phase: string;
  ops: { i: number; at: number; text: string; kind?: LiveSegment['kind']; meta?: LiveSegment['meta'] }[];
}

export function applyLive(prev: LiveState | undefined, msg: LiveMessage): LiveState {
  const segments = prev ? [...prev.segments] : [];
  for (const op of msg.ops) {
    const cur = segments[op.i];
    if (op.kind) segments[op.i] = { kind: op.kind, text: op.text, meta: op.meta ?? {} };
    else if (cur) segments[op.i] = { ...cur, text: cur.text.slice(0, op.at) + op.text };
  }
  return { phase: msg.phase, segments };
}

const STICK_PX = 40; // within this of the bottom, new output keeps the view scrolled down

/** What a running agent is doing right now: reasoning, tool calls and text, as they stream in. */
export default function LiveFeed({ live, components }: { live: LiveState | undefined; components: Components }) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const stickRef = useRef(true);
  useLayoutEffect(() => {
    const box = boxRef.current;
    if (box && stickRef.current) box.scrollTop = box.scrollHeight;
  }, [live]);

  return (
    <section className="live">
      <div className="live-phase">
        <LoaderCircle size={14} className="spin" />
        <span>{live ? `${live.phase[0].toUpperCase()}${live.phase.slice(1)}…` : 'Starting…'}</span>
      </div>
      {live && live.segments.length > 0 && (
        <div
          className="live-feed"
          ref={boxRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < STICK_PX;
          }}
        >
          {live.segments.map((seg, i) =>
            seg.kind === 'thinking' ? (
              <p key={i} className="live-thinking">
                {seg.text}
              </p>
            ) : seg.kind === 'tool' ? (
              <div key={i} className={`live-tool ${seg.meta.status ?? 'running'}${seg.meta.kind === 'discovery' ? ' discovery' : ''}`}>
                {seg.meta.status === 'ok' ? (
                  <Check size={13} strokeWidth={2.5} />
                ) : seg.meta.status === 'failed' ? (
                  <X size={13} strokeWidth={2.5} />
                ) : (
                  <LoaderCircle size={13} className="spin" />
                )}
                <code>{seg.text}</code>
                {seg.meta.args && <span className="live-args">{seg.meta.args}</span>}
              </div>
            ) : (
              <div key={i} className="md live-text">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
                  {seg.text}
                </ReactMarkdown>
              </div>
            ),
          )}
        </div>
      )}
    </section>
  );
}
