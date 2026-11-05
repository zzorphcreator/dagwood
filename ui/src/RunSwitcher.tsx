import { useEffect, useRef, useState } from 'react';
import { fmtCost } from './api';

export interface RunRef {
  id: string;
  task: string;
  status?: string;
  created_at?: number;
  model?: string;
  mode?: string | null; // cost mode; null/absent = one model for all agents
  total_cost_usd?: number;
}

interface Props {
  runs: RunRef[];
  currentId: string | null;
  statusOf: (r: RunRef) => string | undefined; // live status for the open run, polled for others
  onOpen: (id: string) => void;
}

const ACTIVE = new Set(['planning', 'awaiting_approval', 'queued', 'pending', 'running']);

function ago(seconds?: number): string {
  if (!seconds) return '';
  const s = Math.max(0, Date.now() / 1000 - seconds);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const shortModel = (id?: string) => (id ? id.split('/').pop() : '');

export default function RunSwitcher({ runs, currentId, statusOf, onOpen }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as globalThis.Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  const q = query.trim().toLowerCase();
  const matches = runs.filter((r) => !q || r.task.toLowerCase().includes(q) || r.id.includes(q));
  const active = matches.filter((r) => ACTIVE.has(statusOf(r) ?? ''));
  const recent = matches.filter((r) => !ACTIVE.has(statusOf(r) ?? ''));
  const current = runs.find((r) => r.id === currentId);

  const choose = (id: string) => {
    setOpen(false);
    setQuery('');
    onOpen(id);
  };

  const row = (r: RunRef) => {
    const status = statusOf(r) ?? 'unknown';
    return (
      <li key={r.id}>
        <button className={r.id === currentId ? 'switcher-row current' : 'switcher-row'} onClick={() => choose(r.id)}>
          <span className={`dot ${status}`} />
          <span className="row-body">
            <span className="row-task">{r.task}</span>
            <span className="row-meta">
              {status.replace('_', ' ')} · {ago(r.created_at)}
              {r.total_cost_usd ? ` · ${fmtCost(r.total_cost_usd)}` : ''}
              {r.mode ? ` · ${r.mode}` : r.model ? ` · ${shortModel(r.model)}` : ''} · {r.id}
            </span>
          </span>
        </button>
      </li>
    );
  };

  return (
    <div className="run-switcher" ref={ref}>
      <button
        className="switcher-button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={current?.task}
      >
        {current ? (
          <>
            <span className={`dot ${statusOf(current) ?? ''}`} />
            <span className="switcher-title">{current.task}</span>
          </>
        ) : (
          <span className="switcher-title">Runs ({runs.length})</span>
        )}
        <span aria-hidden="true">▾</span>
      </button>
      {open && (
        <div className="switcher-pop" role="dialog" aria-label="Switch run">
          <input
            autoFocus
            className="switcher-search"
            placeholder={`Search ${runs.length} runs…`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setOpen(false);
              if (e.key === 'Enter' && matches[0]) choose((active[0] ?? recent[0]).id);
            }}
          />
          <div className="switcher-list">
            {active.length > 0 && (
              <>
                <div className="switcher-section">Active ({active.length})</div>
                <ul>{active.map(row)}</ul>
              </>
            )}
            {recent.length > 0 && (
              <>
                <div className="switcher-section">Recent</div>
                <ul>{recent.map(row)}</ul>
              </>
            )}
            {matches.length === 0 && <p className="muted switcher-empty">No runs match “{query}”.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
