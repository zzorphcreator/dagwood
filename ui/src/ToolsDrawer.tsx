import { X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { browseCatalog, disableToolkit, enableToolkit, listSources, type CatalogItem, type ToolInfo } from './api';

interface Props {
  enabled: ToolInfo[];
  onClose: () => void;
  onChanged: () => void;
}

const message = (err: unknown) => (err instanceof Error ? err.message : String(err));

export default function ToolsDrawer({ enabled, onClose, onChanged }: Props) {
  const [configured, setConfigured] = useState<boolean | null>(null);
  const [search, setSearch] = useState('');
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listSources()
      .then((s) => setConfigured(s.includes('composio')))
      .catch((err) => {
        setConfigured(false);
        setError(message(err));
      });
  }, []);

  useEffect(() => {
    if (!configured) return;
    let alive = true;
    const timer = setTimeout(() => {
      setLoading(true);
      browseCatalog(search)
        .then((found) => alive && setItems(found))
        .catch((err) => alive && setError(message(err)))
        .finally(() => alive && setLoading(false));
    }, 300);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [search, configured]);

  const enabledIds = new Set(enabled.map((t) => t.id));
  const external = enabled.filter((t) => t.source !== 'builtin');
  const builtins = enabled.filter((t) => t.source === 'builtin');

  const toggle = async (id: string, on: boolean) => {
    setBusyId(id);
    setError(null);
    try {
      await (on ? enableToolkit(id) : disableToolkit(id));
      onChanged();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-label="Tools" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <div>
            <div className="panel-title">Tools</div>
            <div className="panel-sub">Toolkits the planner may assign to agents. You can still override per agent.</div>
          </div>
          <button className="ghost" onClick={onClose} aria-label="Close tools">
            <X size={18} />
          </button>
        </div>

        {error && <div className="drawer-error">{error}</div>}

        <section>
          <h3>Enabled</h3>
          <ul className="toolkit-list">
            {builtins.map((t) => (
              <li key={t.id}>
                <div className="toolkit-main">
                  <code>{t.name}</code> <span className="tag">built-in</span>
                  <div className="muted">{t.description}</div>
                </div>
              </li>
            ))}
            {external.map((t) => (
              <li key={t.id}>
                {t.logo && <img src={t.logo} alt="" className="toolkit-logo" />}
                <div className="toolkit-main">
                  <strong>{t.name}</strong> <span className="tag">{t.source}</span>
                  {t.needs_auth && <span className="tag auth">needs account</span>}
                  <div className="muted">{t.description}</div>
                </div>
                <button className="small danger" disabled={busyId === t.id} onClick={() => void toggle(t.id, false)}>
                  Disable
                </button>
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h3>Composio catalog</h3>
          {configured === false ? (
            <div className="setup-steps">
              <p>
                Optional. Dagwood runs without it, but agents then have only the built-in tools above and no web
                search. To add search and app toolkits (GitHub, Gmail, …):
              </p>
              <ol>
                <li>
                  Create a free account at{' '}
                  <a href="https://dashboard.composio.dev" target="_blank" rel="noopener noreferrer">
                    dashboard.composio.dev
                  </a>
                  .
                </li>
                <li>
                  In your project, open <strong>Settings → API Keys</strong> and copy the key.
                </li>
                <li>
                  Add <code>COMPOSIO_API_KEY=…</code> to <code>.env</code> and restart the backend.
                </li>
                <li>Reopen this panel. Composio Search is enabled for you; enable others from the catalog.</li>
              </ol>
            </div>
          ) : configured === null ? (
            <p className="muted">Checking…</p>
          ) : (
            <>
              <p className="muted setup-note">
                Enable a toolkit to let the planner assign it to agents. Toolkits marked “needs account” ask you to
                connect that account when you review a plan that uses them (keep “Review plan first” on).
              </p>
              <input
                className="drawer-search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search toolkits (github, gmail, search…)"
                aria-label="Search toolkits"
              />
              {loading && <p className="muted">Searching…</p>}
              <ul className="toolkit-list">
                {items.map((t) => {
                  const on = enabledIds.has(t.id);
                  return (
                    <li key={t.id}>
                      {t.logo && <img src={t.logo} alt="" className="toolkit-logo" />}
                      <div className="toolkit-main">
                        <strong>{t.name}</strong>
                        {t.needs_auth && <span className="tag auth">needs account</span>}
                        <div className="muted">{t.description}</div>
                      </div>
                      <button
                        className={on ? 'small danger' : 'small'}
                        disabled={busyId === t.id}
                        onClick={() => void toggle(t.id, !on)}
                      >
                        {on ? 'Disable' : 'Enable'}
                      </button>
                    </li>
                  );
                })}
                {!loading && items.length === 0 && <p className="muted">No toolkits found.</p>}
              </ul>
            </>
          )}
        </section>
      </aside>
    </div>
  );
}
