import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { emptyView, reduce, type GuildEvent } from './market';

// AG Studio and Bryntum are large; load them after the page shell has painted.
const Dashboard = lazy(() => import('./Dashboard'));
const Timeline = lazy(() => import('./Timeline'));
const Loading = ({ what }: { what: string }) => <p className="muted small loading">Loading {what}…</p>;

type Status = 'idle' | 'starting' | 'live' | 'done' | 'error';

export default function App() {
  const [budget, setBudget] = useState('3.00');
  const [events, setEvents] = useState<GuildEvent[]>([]);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const source = useRef<EventSource | null>(null);
  const runBudget = useRef(3);

  const view = useMemo(() => (events.length ? reduce(events, runBudget.current) : emptyView(Number(budget) || 0)),
    [events, budget]);

  const attach = useCallback((runId: string, runBudgetValue?: number) => {
    source.current?.close();
    const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    source.current = es;
    setStatus('live');
    // The previous run stays on screen until this run's first event arrives, so the
    // dashboard never sits empty (AG Studio errors when emptied data is refilled).
    let first = true;
    const onEvent = (msg: MessageEvent) => {
      const event = JSON.parse(msg.data);
      const replace = first;  // read now: React may run the updater after `first` flips
      first = false;
      if (replace && runBudgetValue !== undefined) runBudget.current = runBudgetValue;
      setEvents(prev => (replace ? [event] : [...prev, event]));
    };
    for (const t of ['plan', 'bids', 'awarded', 'work', 'settled', 'blocked', 'unfunded', 'done', 'error', 'payment']) {
      es.addEventListener(t, onEvent as EventListener);
    }
    es.addEventListener('end', () => { es.close(); setStatus('done'); });
    es.onerror = () => { if (es.readyState === EventSource.CLOSED) return; es.close(); setStatus(s => (s === 'done' ? s : 'error')); setError('Stream interrupted. Run again.'); };
  }, []);

  const post = async (path: string) => {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ budget }),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`);
    return body;
  };

  const start = useCallback(async () => {
    setStatus('starting'); setError('');
    try {
      const body = await post('/api/runs');
      attach(body.run_id, Number(budget));
    } catch (e) {
      setStatus('error'); setError(e instanceof Error ? e.message : String(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [budget, attach]);

  // Funded run: PayPal sandbox approval first; PayPal sends the buyer back to /?run=<id>.
  const fund = async () => {
    setStatus('starting'); setError('');
    try {
      const body = await post('/api/fund');
      window.location.assign(body.approve_url);
    } catch (e) {
      setStatus('error'); setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    fetch('/healthz').then(r => r.json()).then(setHealth).catch(() => setHealth(null));
    const params = new URLSearchParams(window.location.search);
    const runId = params.get('run');
    const payError = params.get('payment_error');
    if (runId || payError) window.history.replaceState(null, '', '/');
    if (runId && /^[0-9a-f]{12}$/.test(runId)) attach(runId);  // back from PayPal: watch the funded run
    else {
      start();
      if (payError) setError(payError);  // after start(), which clears old errors
    }
    return () => source.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const busy = status === 'starting' || status === 'live';

  return (
    <div className="page">
      <header className="top">
        <div className="brand">
          <span className="logo" aria-hidden>G</span>
          <div>
            <h1>Guild</h1>
            <p className="tag">An open job market where AI agents hire AI agents. Paid through PayPal only for work that passes review.</p>
          </div>
        </div>
        <form className="controls" onSubmit={e => { e.preventDefault(); if (!busy) start(); }}>
          <label>
            Budget (USD, sandbox)
            <input inputMode="decimal" value={budget} onChange={e => setBudget(e.target.value)} aria-label="Budget in US dollars" />
          </label>
          <button type="submit" disabled={busy}>{busy ? 'Running…' : 'Run the market'}</button>
          {health?.funding && (
            <button type="button" className="paypal" disabled={busy} onClick={fund}
              title="Approve the budget in PayPal sandbox; only work that passes review is captured">
              Fund with PayPal (sandbox)
            </button>
          )}
        </form>
      </header>

      <section className="goal" aria-live="polite">
        <div><span className="k">Goal</span> {view.goal || 'Plan my team offsite in Lisbon: 6 people, 2 days.'}</div>
        <div className="meta">
          <span className={`dot dot-${status}`} /> {status === 'live' ? 'Live' : status === 'done' ? 'Finished' : status === 'error' ? 'Error' : 'Starting'}
          {view.lead && <> · Lead: {view.lead} · Reviewer: {view.reviewer}</>}
          {health && <> · Agents: {health.agents} · Payments: {health.payments}</>}
        </div>
        {error && <div className="error" role="alert">{error}</div>}
      </section>

      <main className="grid">
        <section className="panel story" aria-label="What happened">
          <h2>What happened</h2>
          <ol>{view.story.map((s, i) => (
            <li key={i} className={/not paid|failed/.test(s) ? 'bad' : /paid \$/.test(s) ? 'good' : ''}>
              {s}
              {view.links[i] && <> <a href={view.links[i]} target="_blank" rel="noopener noreferrer">Watch live ↗</a></>}
            </li>
          ))}</ol>
          {!view.story.length && <p className="muted">Waiting for the first event…</p>}
        </section>

        <section className="panel studio" aria-label="Market dashboard">
          <div className="panel-head">
            <h2>Market dashboard <span className="by">AG Studio</span></h2>
            <div className="seg" role="group" aria-label="Dashboard mode">
              <button className={mode === 'view' ? 'on' : ''} onClick={() => setMode('view')}>View</button>
              <button className={mode === 'edit' ? 'on' : ''} onClick={() => setMode('edit')}>Edit</button>
            </div>
          </div>
          <div className="studio-scroll"><div className="studio-box">
            <Suspense fallback={<Loading what="the AG Studio dashboard" />}><Dashboard view={view} mode={mode} /></Suspense>
          </div></div>
        </section>

        <section className="panel timeline" aria-label="Job timeline">
          <h2>Job timeline <span className="by">Bryntum Gantt</span></h2>
          <p className="muted small">Minutes of agent work. Green = paid, red = failed review (unpaid), blue = working, grey = never ran.</p>
          <div className="gantt-box"><Suspense fallback={<Loading what="the Bryntum timeline" />}><Timeline view={view} /></Suspense></div>
        </section>
      </main>

      <footer className="foot">
        PayPal sandbox only. Agent tools show “simulated” until their keys are added. <a href="/api/docs">API</a> ·{' '}
        <a href="https://github.com/Bigreddroid/guild">Source (MIT)</a>
      </footer>
    </div>
  );
}
