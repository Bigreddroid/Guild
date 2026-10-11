import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AgStudio } from 'ag-studio-react';
import { emptyView, reduce, type GuildEvent } from './market';
import { guildTheme, initialState, studioData, widgets } from './studio';
import Timeline from './Timeline';

type Status = 'idle' | 'starting' | 'live' | 'done' | 'error';

export default function App() {
  const [budget, setBudget] = useState('3.00');
  const [events, setEvents] = useState<GuildEvent[]>([]);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [health, setHealth] = useState<Record<string, string> | null>(null);
  const source = useRef<EventSource | null>(null);
  const runBudget = useRef(3);

  const view = useMemo(() => (events.length ? reduce(events, runBudget.current) : emptyView(Number(budget) || 0)),
    [events, budget]);
  const data = useMemo(() => studioData(view), [view]);

  const start = useCallback(async () => {
    source.current?.close();
    setStatus('starting'); setError(''); setEvents([]);
    try {
      const r = await fetch('/api/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ budget }),
      });
      const body = await r.json();
      if (!r.ok) throw new Error(body.detail ?? `HTTP ${r.status}`);
      runBudget.current = Number(budget);
      const es = new EventSource(`/api/runs/${body.run_id}/events`);
      source.current = es;
      setStatus('live');
      es.onmessage = null;
      const onEvent = (msg: MessageEvent) => setEvents(prev => [...prev, JSON.parse(msg.data)]);
      for (const t of ['plan', 'bids', 'awarded', 'work', 'settled', 'blocked', 'unfunded', 'done']) {
        es.addEventListener(t, onEvent as EventListener);
      }
      es.addEventListener('end', () => { es.close(); setStatus('done'); });
      es.onerror = () => { if (es.readyState === EventSource.CLOSED) return; es.close(); setStatus(s => (s === 'done' ? s : 'error')); setError('Stream interrupted. Run again.'); };
    } catch (e) {
      setStatus('error'); setError(e instanceof Error ? e.message : String(e));
    }
  }, [budget]);

  useEffect(() => {
    fetch('/healthz').then(r => r.json()).then(setHealth).catch(() => setHealth(null));
    start();
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
          <ol>{view.story.map((s, i) => <li key={i} className={/not paid|failed/.test(s) ? 'bad' : /paid \$/.test(s) ? 'good' : ''}>{s}</li>)}</ol>
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
            <AgStudio style={{ height: '100%' }} panels={{ view: { right: [] } } as any} data={data as any} initialState={initialState as any} mode={mode} theme={guildTheme as any} widgets={widgets as any} />
          </div></div>
        </section>

        <section className="panel timeline" aria-label="Job timeline">
          <h2>Job timeline <span className="by">Bryntum Gantt</span></h2>
          <p className="muted small">Minutes of agent work. Green = paid, red = failed review (unpaid), blue = working.</p>
          <div className="gantt-box"><Timeline view={view} /></div>
        </section>
      </main>

      <footer className="foot">
        PayPal sandbox only. Agent tools show “simulated” until their keys are added. <a href="/api/docs">API</a> ·{' '}
        <a href="https://github.com/Bigreddroid/guild">Source (MIT)</a>
      </footer>
    </div>
  );
}
