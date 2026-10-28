import { useCallback, useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { ApiError, credits, humanize, request, SESSION_EXPIRED, setCsrfToken } from './api';
import type { AuctionEvent, Bidder, Run, RunConfig, RunSummary, Session, User } from './api';
import AuthScreen from './AuthScreen';

const defaults: RunConfig = {
  item_name: 'GPU time allocation',
  reserve_price: 400,
  bidders: [
    { id: 'bidder_1', name: 'Aster', valuation: 1000, budget: 1000 },
    { id: 'bidder_2', name: 'Birch', valuation: 900, budget: 1000 },
    { id: 'bidder_3', name: 'Cedar', valuation: 1200, budget: 1500 },
  ],
};

const statusLabel: Record<Run['status'], string> = {
  created: 'Ready to open',
  collecting: 'Collecting bids',
  closed: 'Ready to settle',
  settled: 'Settled',
};
const errorText = (error: unknown) =>
  error instanceof Error ? error.message : 'Something went wrong. Please try again.';

function Setup({
  onCreate,
  busy,
  users,
}: {
  onCreate: (config: RunConfig) => void;
  busy: boolean;
  users: User[];
}) {
  const [config, setConfig] = useState<RunConfig>(defaults);
  const updateBidder = (index: number, key: keyof Bidder, value: string) =>
    setConfig((old) => ({
      ...old,
      bidders: old.bidders.map((bidder, i) =>
        i === index
          ? {
              ...bidder,
              [key]:
                key === 'name' ? value : key === 'owner_user_id' ? value || null : Number(value),
            }
          : bidder,
      ),
    }));
  function submit(event: FormEvent) {
    event.preventDefault();
    onCreate(config);
  }
  return (
    <form className="setup panel" onSubmit={submit}>
      <div className="panel-heading">
        <div>
          <span className="eyebrow">EXPERIMENT CONFIGURATION</span>
          <h2>Set up an auction</h2>
        </div>
        <span className="version-tag">v0</span>
      </div>
      <div className="form-grid">
        <label>
          Item name
          <input
            value={config.item_name}
            required
            maxLength={120}
            onChange={(e) => setConfig({ ...config, item_name: e.target.value })}
          />
        </label>
        <label>
          Reserve price <span className="unit">credits</span>
          <input
            type="number"
            min="0"
            max="1000000000"
            step="1"
            required
            value={config.reserve_price}
            onChange={(e) => setConfig({ ...config, reserve_price: Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="subheading">
        <h3>Scripted bidders</h3>
        <span>Whole-number credits</span>
      </div>
      <p className="field-note">
        Each bidder submits 80% of its private valuation, rounded down. If its budget cannot cover
        that bid, it abstains.
      </p>
      <div className="bidder-inputs">
        {config.bidders.map((bidder, index) => (
          <fieldset className="bidder-form" key={bidder.id}>
            <legend>Bidder {index + 1}</legend>
            <span className="avatar">{String(index + 1).padStart(2, '0')}</span>
            <label>
              <span className="input-label">Name</span>
              <input
                aria-label={`Bidder ${index + 1} name`}
                required
                maxLength={60}
                value={bidder.name}
                onChange={(e) => updateBidder(index, 'name', e.target.value)}
              />
            </label>
            <label>
              <span className="input-label">Valuation</span>
              <input
                aria-label={`Bidder ${index + 1} valuation`}
                type="number"
                min="1"
                max="1000000000"
                step="1"
                required
                value={bidder.valuation}
                onChange={(e) => updateBidder(index, 'valuation', e.target.value)}
              />
            </label>
            <label>
              <span className="input-label">Budget</span>
              <input
                aria-label={`Bidder ${index + 1} budget`}
                type="number"
                min="0"
                max="1000000000"
                step="1"
                required
                value={bidder.budget}
                onChange={(e) => updateBidder(index, 'budget', e.target.value)}
              />
            </label>
            <button
              type="button"
              className="icon-button remove-bidder"
              aria-label={`Remove bidder ${index + 1}`}
              disabled={config.bidders.length <= 2 || busy}
              onClick={() =>
                setConfig({ ...config, bidders: config.bidders.filter((_, i) => i !== index) })
              }
            >
              ×
            </button>
            <label className="owner-select">
              <span className="input-label">Assigned account</span>
              <select
                aria-label={`Bidder ${index + 1} account`}
                value={bidder.owner_user_id ?? ''}
                disabled={busy}
                onChange={(e) => updateBidder(index, 'owner_user_id', e.target.value)}
              >
                <option value="">Unassigned · administrator only</option>
                {users.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.username}
                    {user.role === 'admin' ? ' (admin)' : ''}
                  </option>
                ))}
              </select>
            </label>
          </fieldset>
        ))}
      </div>
      <div className="setup-actions">
        <button
          className="button secondary"
          type="button"
          disabled={config.bidders.length >= 8 || busy}
          onClick={() => {
            const next = Math.max(...config.bidders.map((b) => Number(b.id.split('_')[1]))) + 1;
            setConfig({
              ...config,
              bidders: [
                ...config.bidders,
                { id: `bidder_${next}`, name: `Bidder ${next}`, valuation: 1000, budget: 1000 },
              ],
            });
          }}
        >
          + Add bidder
        </button>
        <button className="button primary" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create auction →'}
        </button>
      </div>
    </form>
  );
}

function Metrics({ run, isAdmin }: { run: Run; isAdmin: boolean }) {
  const winner = run.bidders.find((b) => b.id === run.results?.winner_id);
  const winnerOutcome = run.results?.outcomes.find((outcome) => outcome.won);
  return (
    <div className="metrics" aria-label="Auction results">
      <div>
        <span className="metric-label">{run.results ? 'Winner' : 'Responses collected'}</span>
        <strong>
          {run.results
            ? (winner?.name ?? 'No sale')
            : `${run.responded.length} / ${run.bidders.length}`}
        </strong>
        <small>{run.results ? 'Highest eligible bid' : 'Awaiting all bidder responses'}</small>
      </div>
      <div>
        <span className="metric-label">Seller revenue</span>
        <strong>
          {run.results ? credits(run.results.revenue) : '—'}
          <em>credits</em>
        </strong>
        <small>Winner pays their own bid</small>
      </div>
      <div>
        <span className="metric-label">{isAdmin ? 'Winner surplus' : 'Your surplus'}</span>
        <strong>
          {isAdmin
            ? winnerOutcome
              ? credits(winnerOutcome.utility)
              : run.results
                ? '0'
                : '—'
            : run.results
              ? credits(run.results.outcomes.reduce((sum, outcome) => sum + outcome.utility, 0))
              : '—'}
          <em>credits</em>
        </strong>
        <small>{isAdmin ? 'Valuation less payment' : 'Total for your assigned bidders'}</small>
      </div>
    </div>
  );
}

function BidderResults({ run, user }: { run: Run; user: User }) {
  return (
    <div className="table-scroll">
      <table className="results-table">
        <thead>
          <tr>
            <th>Bidder</th>
            <th>Valuation</th>
            <th>Budget</th>
            <th>Bid</th>
            <th>Payment</th>
            <th>Surplus</th>
          </tr>
        </thead>
        <tbody>
          {run.bidders.map((bidder, index) => {
            const result = run.results?.outcomes.find((outcome) => outcome.bidder_id === bidder.id);
            const canRead = user.role === 'admin' || bidder.owner_user_id === user.id;
            const submitted = run.bids?.[bidder.id];
            return (
              <tr key={bidder.id} className={result?.won ? 'winner-row' : ''}>
                <td>
                  <span className="bidder-name">
                    <span className="avatar small">{String(index + 1).padStart(2, '0')}</span>
                    {bidder.name}
                    {bidder.owner_user_id === user.id && (
                      <span className="winner-label">YOURS</span>
                    )}
                    {result?.won && <span className="winner-label">WINNER</span>}
                  </span>
                </td>
                <td>{credits(bidder.valuation)}</td>
                <td>{credits(bidder.budget)}</td>
                <td>
                  {!canRead ? (
                    <span className="sealed">Private</span>
                  ) : result ? (
                    result.bid === null ? (
                      <span className="muted">Abstained</span>
                    ) : (
                      credits(result.bid)
                    )
                  ) : submitted !== undefined ? (
                    credits(submitted)
                  ) : (
                    <span className="sealed">
                      {run.responded.includes(bidder.id) ? 'Abstained' : 'Waiting'}
                    </span>
                  )}
                </td>
                <td>
                  {!canRead ? (
                    <span className="sealed">Private</span>
                  ) : result ? (
                    credits(result.payment)
                  ) : (
                    '—'
                  )}
                </td>
                <td className={result && result.utility > 0 ? 'positive' : ''}>
                  {!canRead ? (
                    <span className="sealed">Private</span>
                  ) : result ? (
                    credits(result.utility)
                  ) : (
                    '—'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function describeEvent(event: AuctionEvent): string {
  const payload = event.payload;
  if (typeof payload.reason === 'string') return payload.reason;
  if (typeof payload.message === 'string') return payload.message;
  if (event.payload.redacted === true)
    return 'Private bidder message recorded. Details are not visible to this account.';
  const fields = Object.entries(payload)
    .filter(([key]) => !['bidders', 'results', 'outcomes', 'config'].includes(key))
    .slice(0, 3);
  return (
    fields
      .map(
        ([key, value]) =>
          `${humanize(key)}: ${typeof value === 'object' ? JSON.stringify(value) : String(value)}`,
      )
      .join(' · ') || 'State transition recorded by the receiver.'
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState('');
  const [logoutBusy, setLogoutBusy] = useState(false);
  const authenticate = useCallback((next: Session) => {
    setCsrfToken(next.csrf_token);
    setSession(next);
    setNotice('');
  }, []);

  useEffect(() => {
    let active = true;
    request<Session>('/auth/me')
      .then((next) => {
        if (active) authenticate(next);
      })
      .catch((cause) => {
        if (active && !(cause instanceof ApiError && cause.status === 401))
          setNotice(`Cannot reach the backend. ${errorText(cause)}`);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    const expired = () => {
      setCsrfToken('');
      setSession(null);
      setNotice('Your session has expired. Please sign in again.');
    };
    window.addEventListener(SESSION_EXPIRED, expired);
    return () => {
      active = false;
      window.removeEventListener(SESSION_EXPIRED, expired);
      setCsrfToken('');
    };
  }, [authenticate]);

  async function logout() {
    setLogoutBusy(true);
    try {
      await request<{ ok: boolean }>('/auth/logout', { method: 'POST' });
      setCsrfToken('');
      setSession(null);
      setNotice('');
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) {
        setCsrfToken('');
        setSession(null);
        setNotice('');
      } else setNotice(`Sign-out failed. ${errorText(cause)}`);
    } finally {
      setLogoutBusy(false);
    }
  }

  if (loading)
    return (
      <div className="session-loading" role="status">
        Loading your workspace…
      </div>
    );
  if (!session) return <AuthScreen onAuthenticated={authenticate} notice={notice} />;
  return (
    <Workbench
      key={session.user.id}
      session={session}
      onLogout={() => void logout()}
      logoutBusy={logoutBusy}
      sessionNotice={notice}
    />
  );
}

function Workbench({
  session,
  onLogout,
  logoutBusy,
  sessionNotice,
}: {
  session: Session;
  onLogout: () => void;
  logoutBusy: boolean;
  sessionNotice: string;
}) {
  const isAdmin = session.user.role === 'admin';
  const [users, setUsers] = useState<User[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [history, setHistory] = useState<RunSummary[]>([]);
  const [events, setEvents] = useState<AuctionEvent[]>([]);
  const [busy, setBusy] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState('');
  const [connection, setConnection] = useState<'connecting' | 'live' | 'polling'>('connecting');
  const [replay, setReplay] = useState<Run | null>(null);
  const [replayThrough, setReplayThrough] = useState<number | null>(null);
  const [replayLoading, setReplayLoading] = useState(false);
  const activeId = useRef<string | null>(null);
  const replayRequest = useRef(0);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      activeId.current = null;
      ++replayRequest.current;
    };
  }, []);

  const refreshHistory = useCallback(async () => {
    const response = await request<{ runs: RunSummary[] }>('/runs');
    if (mounted.current) setHistory(response.runs);
  }, []);
  useEffect(() => {
    refreshHistory().catch((e) => setError(`Cannot reach the backend. ${errorText(e)}`));
    if (isAdmin)
      request<{ users: User[] }>('/admin/users')
        .then((response) => {
          if (mounted.current) setUsers(response.users);
        })
        .catch((cause) => setError(errorText(cause)));
  }, [refreshHistory, isAdmin]);
  useEffect(() => {
    const timer = setInterval(() => {
      void refreshHistory().catch(() => {});
      if (isAdmin)
        void request<{ users: User[] }>('/admin/users')
          .then((response) => {
            if (mounted.current) setUsers(response.users);
          })
          .catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, [refreshHistory, isAdmin]);

  const refresh = useCallback(async (id: string) => {
    const [next, log] = await Promise.all([
      request<Run>(`/runs/${id}`),
      request<{ events: AuctionEvent[] }>(`/runs/${id}/events`),
    ]);
    if (activeId.current !== id) return;
    setRun((old) => (old?.id === id && old.event_count > next.event_count ? old : next));
    setEvents((old) => ((old.at(-1)?.seq ?? 0) > (log.events.at(-1)?.seq ?? 0) ? old : log.events));
    setHistory((old) =>
      old.map((item) => (item.id === id ? { ...item, status: next.status } : item)),
    );
  }, []);

  const runId = run?.id;
  useEffect(() => {
    if (!runId) return;
    let stopped = false;
    let socket: WebSocket | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let delay = 1000;
    let after = 0;
    function connect() {
      if (stopped) return;
      setConnection('connecting');
      const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
      socket = new WebSocket(
        `${protocol}://${window.location.host}/api/runs/${runId}/ws?after=${after}`,
      );
      socket.onopen = () => {
        if (!stopped) {
          setConnection('live');
          delay = 1000;
        }
      };
      socket.onmessage = (event) => {
        if (stopped) return;
        try {
          const message = JSON.parse(event.data) as { type: string; events?: AuctionEvent[] };
          if (message.type === 'events' && message.events?.length) {
            after = Math.max(after, ...message.events.map((item) => item.seq));
            void refresh(runId!).catch(() => {});
          }
        } catch {
          /* REST refresh remains available if a stream packet is malformed. */
        }
      };
      socket.onclose = () => {
        if (!stopped) {
          setConnection('polling');
          retry = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 10000);
        }
      };
      socket.onerror = () => socket?.close();
    }
    connect();
    const poll = setInterval(() => void refresh(runId).catch(() => {}), 3000);
    return () => {
      stopped = true;
      clearTimeout(retry);
      clearInterval(poll);
      socket?.close();
    };
  }, [runId, refresh]);

  async function create(config: RunConfig) {
    if (!isAdmin) return;
    setBusy(true);
    setError('');
    setPlaying(false);
    try {
      const next = await request<Run>('/runs', { method: 'POST', body: JSON.stringify(config) });
      if (!mounted.current) return;
      activeId.current = next.id;
      setRun(next);
      setEvents([]);
      setReplay(null);
      setReplayThrough(null);
      setReplayLoading(false);
      ++replayRequest.current;
      await Promise.all([refresh(next.id), refreshHistory()]);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  async function select(id: string) {
    setBusy(true);
    setError('');
    setPlaying(false);
    setRun(null);
    setEvents([]);
    setReplay(null);
    setReplayThrough(null);
    setReplayLoading(false);
    ++replayRequest.current;
    activeId.current = id;
    try {
      await refresh(id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const advance = useCallback(async () => {
    const id = activeId.current;
    if (!id || !isAdmin) return;
    setBusy(true);
    setError('');
    try {
      const next = await request<Run>(`/runs/${id}/advance`, { method: 'POST' });
      if (activeId.current !== id) return;
      setRun(next);
      if (next.status === 'settled') setPlaying(false);
      await refresh(id);
    } catch (e) {
      setError(errorText(e));
      setPlaying(false);
    } finally {
      setBusy(false);
    }
  }, [refresh, isAdmin]);

  useEffect(() => {
    if (!playing || busy || !run || run.status === 'settled' || replayThrough !== null) return;
    const timer = setTimeout(() => void advance(), 600);
    return () => clearTimeout(timer);
  }, [playing, busy, run, replayThrough, advance]);

  async function seek(through: number) {
    if (!run) return;
    setPlaying(false);
    setReplayThrough(through);
    setReplay(null);
    setReplayLoading(true);
    const revision = ++replayRequest.current;
    const id = run.id;
    try {
      const result = await request<Run>(`/runs/${id}/replay?through=${through}`);
      if (revision === replayRequest.current && activeId.current === id) {
        setReplay(result);
        setError('');
      }
    } catch (e) {
      if (revision === replayRequest.current) {
        setError(errorText(e));
        setReplayThrough(null);
        setReplay(null);
      }
    } finally {
      if (revision === replayRequest.current) setReplayLoading(false);
    }
  }

  const shown = replayThrough !== null ? replay : run;
  const visibleEvents = events.filter(
    (event) => replayThrough === null || event.seq <= replayThrough,
  );
  const replayMax = events.at(-1)?.seq ?? run?.event_count ?? 0;
  const phases: Run['status'][] = ['created', 'collecting', 'closed', 'settled'];
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="brand-mark" aria-hidden="true">
            <i />
            <i />
            <i />
          </span>
          <span>
            Auction<span className="brand-light">Lab</span>
            <small>HAN2601 / RESEARCH SIMULATOR</small>
          </span>
        </a>
        {isAdmin && (
          <div className="sidebar-section">
            <span className="eyebrow">WORKSPACE</span>
            <button
              className="nav-active"
              onClick={() => {
                setPlaying(false);
                setReplay(null);
                setReplayThrough(null);
                setReplayLoading(false);
                setRun(null);
                setEvents([]);
                activeId.current = null;
                ++replayRequest.current;
              }}
              disabled={busy}
            >
              <span>＋</span> New experiment
            </button>
          </div>
        )}
        <div className="history-heading">
          <span className="eyebrow">{isAdmin ? 'ALL AUCTION RUNS' : 'YOUR AUCTION RUNS'}</span>
          <span className="history-count">{history.length}</span>
        </div>
        <nav className="history" aria-label="Saved auction runs">
          {history.length === 0 ? (
            <p className="history-empty">
              {isAdmin
                ? 'Your experiments will appear here. Every run is saved locally.'
                : 'Assigned auctions will appear here.'}
            </p>
          ) : (
            history.map((item) => (
              <button
                key={item.id}
                className={`history-item ${run?.id === item.id ? 'selected' : ''}`}
                onClick={() => void select(item.id)}
                disabled={busy}
              >
                <span className={`status-dot ${item.status}`} />
                <span>
                  <strong>{item.item_name}</strong>
                  <small>
                    {new Date(item.created_at).toLocaleDateString(undefined, {
                      month: 'short',
                      day: 'numeric',
                    })}{' '}
                    · {statusLabel[item.status] ?? item.status}
                  </small>
                </span>
              </button>
            ))
          )}
        </nav>
        <div className="sidebar-footer">
          <span className="version-pill">VERSION 0.1</span>
          <p>
            Scripted agents.
            <br />
            Transparent decisions.
          </p>
          <small>LIU Xianlong · MA Ho Tsun Matthew</small>
        </div>
      </aside>

      <main>
        <header className="page-header">
          <div>
            <div className="eyebrow">EXPERIMENT WORKBENCH</div>
            <h1>Watch a strategy become an outcome.</h1>
            <p>
              {isAdmin
                ? 'Configure an auction. Follow each message. Reproduce the result.'
                : 'Follow your assigned bidders and inspect your results.'}
            </p>
          </div>
          <div className="account-menu">
            <span className="account-name">
              {session.user.username}
              <small>{isAdmin ? 'Administrator' : 'Participant'}</small>
            </span>
            <button className="button secondary" disabled={logoutBusy || busy} onClick={onLogout}>
              {logoutBusy ? 'Signing out…' : 'Sign out'}
            </button>
          </div>
        </header>
        <div className="mechanism-strip">
          <span>
            <strong>01</strong> First-price sealed-bid
          </span>
          <span>Single item</span>
          <span>Fixed 80% strategy</span>
          <span className="muted">No LLM calls</span>
        </div>
        {(error || sessionNotice) && (
          <div className="error-banner" role="alert">
            <span>{error || sessionNotice}</span>
            <button className="text-button" onClick={() => setError('')}>
              Dismiss
            </button>
          </div>
        )}

        {!run && !isAdmin ? (
          <section className="panel participant-empty">
            <span className="eyebrow">YOUR WORKSPACE</span>
            <h2>{history.length ? 'Choose an assigned auction' : 'No assigned auctions yet'}</h2>
            <p>
              {history.length
                ? 'Select an auction from the sidebar to follow your scripted bidders. Only your private bids and results are visible to your account.'
                : 'Ask your administrator to assign a bidder to your account. Assigned auctions will appear automatically.'}
            </p>
          </section>
        ) : !run ? (
          <div className="setup-layout">
            <Setup onCreate={(config) => void create(config)} busy={busy} users={users} />
            <aside className="guide">
              <span className="eyebrow">HOW THIS RUN WORKS</span>
              <h2>
                One receiver.
                <br />A complete audit trail.
              </h2>
              <ol>
                <li>
                  <strong>Open the auction</strong>
                  <p>The auctioneer announces the item and rules.</p>
                </li>
                <li>
                  <strong>Collect sealed bids</strong>
                  <p>The receiver gives each bidder a turn and checks its response.</p>
                </li>
                <li>
                  <strong>Close and settle</strong>
                  <p>The highest eligible bidder pays its bid. Others pay zero.</p>
                </li>
              </ol>
              <p className="guide-note">
                Equal bids are resolved by the configured bidder order. The reserve price must be
                met for a sale.
              </p>
            </aside>
          </div>
        ) : (
          <div className="run-layout">
            <section className="panel auction-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">
                    {replayThrough !== null ? 'REPLAY SNAPSHOT' : 'ACTIVE EXPERIMENT'}
                  </span>
                  <h2>{run.item_name}</h2>
                  <span className="run-id">
                    RUN {run.id.slice(0, 8)} · Reserve {credits(run.reserve_price)} credits
                  </span>
                </div>
                <span className={`state-badge ${shown?.status}`}>
                  {shown ? statusLabel[shown.status] : 'Loading snapshot…'}
                </span>
              </div>
              <div className="phase-track" aria-label="Auction progress">
                {phases.map((phase, index) => (
                  <div
                    className={shown && phases.indexOf(shown.status) >= index ? 'reached' : ''}
                    key={phase}
                  >
                    <span>{index + 1}</span>
                    {['Ready', 'Bidding', 'Closed', 'Settled'][index]}
                  </div>
                ))}
              </div>
              {shown && (
                <>
                  <Metrics run={shown} isAdmin={isAdmin} />
                  <BidderResults run={shown} user={session.user} />
                </>
              )}
              {replayLoading && (
                <p className="empty-events" role="status">
                  Loading state at event {replayThrough}…
                </p>
              )}
              <p className="observer-note">
                {isAdmin
                  ? 'Administrator view: all submitted bids, valuations, budgets and results are visible. Scripted bidders still receive only their own private information.'
                  : 'Participant view: only your assigned bidders’ private values, bids and results are visible. Other bidders’ amounts remain private, including after settlement.'}
              </p>
              <div className="run-actions">
                <div className="stream-label">
                  <span className={connection === 'live' ? 'live-dot' : 'waiting-dot'} />
                  {connection === 'live'
                    ? 'Event stream connected'
                    : connection === 'polling'
                      ? 'Refreshing via REST · reconnecting'
                      : 'Connecting event stream…'}
                </div>
                {isAdmin && (
                  <div className="button-group">
                    <button
                      className="button secondary"
                      disabled={
                        busy || playing || run.status === 'settled' || replayThrough !== null
                      }
                      onClick={() => void advance()}
                    >
                      Step once →
                    </button>
                    <button
                      className="button primary"
                      disabled={
                        run.status === 'settled' || replayThrough !== null || (busy && !playing)
                      }
                      onClick={() => setPlaying((value) => !value)}
                    >
                      {playing ? 'Ⅱ Pause' : '▶ Auto-play'}
                    </button>
                  </div>
                )}
              </div>
            </section>

            {run.status === 'settled' && (
              <section className="panel replay-panel">
                <div>
                  <span className="eyebrow">REPRODUCIBILITY</span>
                  <h3>Replay this auction</h3>
                  <p>Inspect the state after each recorded event.</p>
                </div>
                <div className="replay-control">
                  <label htmlFor="replay">
                    Event <strong>{replayThrough ?? replayMax}</strong> / {replayMax}
                  </label>
                  <input
                    id="replay"
                    type="range"
                    min={events[0]?.seq ?? 1}
                    max={replayMax}
                    value={replayThrough ?? replayMax}
                    onChange={(e) => void seek(Number(e.target.value))}
                  />
                  <button
                    className="text-button"
                    onClick={() => {
                      ++replayRequest.current;
                      setReplay(null);
                      setReplayThrough(null);
                      setReplayLoading(false);
                    }}
                  >
                    Return to final result
                  </button>
                </div>
                <a
                  className="button secondary"
                  href={`/api/runs/${run.id}/export`}
                  download={`auction-${run.id}.json`}
                >
                  Export JSON ↗
                </a>
              </section>
            )}

            <section className="panel event-panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">MESSAGE TRACE</span>
                  <h2>Every step, recorded.</h2>
                </div>
                <span className="event-total">{visibleEvents.length} events</span>
              </div>
              <ol className="event-log">
                {visibleEvents.map((event) => (
                  <li key={event.seq}>
                    <span className="event-number">{String(event.seq).padStart(2, '0')}</span>
                    <div className="event-content">
                      <div>
                        <strong>{humanize(event.type)}</strong>
                        <span className="event-actor">{event.actor}</span>
                      </div>
                      <p>{describeEvent(event)}</p>
                    </div>
                    <time>
                      {new Date(event.created_at).toLocaleTimeString(undefined, {
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                      })}
                    </time>
                  </li>
                ))}
              </ol>
              {visibleEvents.length === 0 && (
                <p className="empty-events">No messages recorded at this point.</p>
              )}
            </section>
          </div>
        )}
        <footer className="page-footer">
          <span>Generic Auction Simulator · Final Year Project</span>
          <span>Deterministic rules. Persisted events. Repeatable results.</span>
        </footer>
      </main>
    </div>
  );
}
