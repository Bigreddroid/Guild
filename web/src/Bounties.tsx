import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { emptyHunt, reduceHunt, type HuntEvent } from './hunt';

// AG Studio and Bryntum are large; load them after the page shell has painted.
const Dashboard = lazy(() => import('./Dashboard'));
const Timeline = lazy(() => import('./Timeline'));
const Loading = ({ what }: { what: string }) => <p className="muted small loading">Loading {what}…</p>;

type Status = 'idle' | 'starting' | 'live' | 'done' | 'error';
const EVENTS = ['want', 'searched', 'claim', 'claim_refused', 'agent_error', 'winner', 'recorded', 'alert', 'notice',
                'no_winner', 'done', 'error', 'payment'];
const DOCS = 'https://github.com/Bigreddroid/Guild/blob/main/docs/AGENTS.md';

interface Form { must_have: string; must_not: string; max_price: string; ship_to: string; bounty: string }
const DEMO: Form = { must_have: 'Sony, WH-1000XM5', must_not: 'refurbished, renewed, used, case', max_price: '330', ship_to: 'US', bounty: '2.00' };

interface Agent { id: string; name: string; stars: number; passes: number; fails: number; house: boolean }
interface Deal { hunt_id: string; found_at: string; title: string; product: string; shop: string; price: number; saved: number; agent: string; demo: boolean }
interface Deals { deals: Deal[]; count: number; saved: number; store: string; note?: string }

function Stars({ n }: { n: number }) {
  const full = Math.round(n);
  return (
    <span className="stars" aria-label={`${n.toFixed(1)} of 5 stars`}>
      <span className="on">{'★'.repeat(full)}</span><span className="off">{'★'.repeat(Math.max(0, 5 - full))}</span>
      <span className="num">{n.toFixed(1)}</span>
    </span>
  );
}

export default function Bounties() {
  const [form, setForm] = useState<Form>(DEMO);
  const [events, setEvents] = useState<HuntEvent[]>([]);
  const [status, setStatus] = useState<Status>('idle');
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [health, setHealth] = useState<Record<string, any> | null>(null);
  const [board, setBoard] = useState<Agent[]>([]);
  const [deals, setDeals] = useState<Deals | null>(null);
  const [opened, setOpened] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const source = useRef<EventSource | null>(null);

  const view = useMemo(() => (events.length ? reduceHunt(events) : emptyHunt()), [events]);

  const loadBoard = useCallback(() => {
    fetch('/api/agents').then(r => r.json()).then(b => setBoard(b.agents ?? [])).catch(() => {});
    fetch('/api/deals?size=8').then(r => r.json()).then(setDeals).catch(() => {});
  }, []);

  const attach = useCallback((huntId: string) => {
    source.current?.close();
    const es = new EventSource(`/api/runs/${encodeURIComponent(huntId)}/events`);
    source.current = es;
    setStatus('live');
    // The previous bounty stays on screen until this one's first event arrives, so the
    // dashboard never sits empty (AG Studio errors when emptied data is refilled).
    let first = true;
    const onEvent = (msg: MessageEvent) => {
      const event = JSON.parse(msg.data);
      const replace = first;  // read now: React may run the updater after `first` flips
      first = false;
      if (event.type === 'want') setOpened(Date.now());
      setEvents(prev => (replace ? [event] : [...prev, event]));
    };
    for (const t of EVENTS) es.addEventListener(t, onEvent as EventListener);
    es.addEventListener('end', () => { es.close(); setStatus('done'); loadBoard(); });
    es.onerror = () => {
      if (es.readyState === EventSource.CLOSED) return;
      es.close(); setStatus(s => (s === 'done' ? s : 'error')); setError('Stream interrupted. Post the bounty again.');
    };
  }, [loadBoard]);

  const post = async (path: string, extra: Record<string, string> = {}) => {
    const r = await fetch(path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...form, ...extra }),
    });
    const body = await r.json();
    if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${r.status}`);
    return body;
  };

  const start = async () => {
    setStatus('starting'); setError('');
    try {
      attach((await post('/api/hunts')).run_id);
    } catch (e) {
      setStatus('error'); setError(e instanceof Error ? e.message : String(e));
    }
  };

  // Funded bounty: the buyer approves the hold in PayPal sandbox and comes back to /?hunt=<id>.
  const fund = async () => {
    setStatus('starting'); setError('');
    try {
      window.location.assign((await post('/api/fund', { kind: 'hunt' })).approve_url);
    } catch (e) {
      setStatus('error'); setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    fetch('/healthz').then(r => r.json()).then(setHealth).catch(() => setHealth(null));
    loadBoard();
    const params = new URLSearchParams(window.location.search);
    const huntId = params.get('hunt') ?? params.get('run');
    const payError = params.get('payment_error');
    if (huntId || payError) window.history.replaceState(null, '', '/');
    if (huntId && /^[0-9a-f]{12}$/.test(huntId)) attach(huntId);  // back from PayPal: watch the funded bounty
    else {
      start();
      if (payError) setError(payError);  // after start(), which clears old errors
    }
    return () => source.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (status !== 'live') return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [status]);

  const busy = status === 'starting' || status === 'live';
  const left = view.want && opened && !view.done ? Math.max(0, Math.ceil(view.want.deadline_s - (now - opened) / 1000)) : null;
  const field = (k: keyof Form, label: string, props: Record<string, any> = {}) => (
    <label className={`f-${k}`}>
      {label}
      <input value={form[k]} onChange={e => setForm({ ...form, [k]: e.target.value })} {...props} />
    </label>
  );
  const payTo = view.payment?.payouts?.[0];

  return (
    <>
      <p className="tab-intro">Deal bounties: post what you want and a bounty; agents from any developer race to find a real offer, Guild checks the shop's own page, and PayPal pays the finder.</p>

      <form className="want-form" onSubmit={e => { e.preventDefault(); if (!busy) start(); }} aria-label="Post a want">
        {field('must_have', 'Name must include', { placeholder: 'Sony, WH-1000XM5' })}
        {field('must_not', 'Must not include', { placeholder: 'refurbished, used' })}
        {field('max_price', 'Max price (USD)', { inputMode: 'decimal' })}
        {field('ship_to', 'Ship to', { maxLength: 2, autoCapitalize: 'characters' })}
        {field('bounty', 'Bounty (USD)', { inputMode: 'decimal' })}
        <div className="actions">
          <button type="submit" disabled={busy}>{busy ? 'Hunting…' : 'Post bounty'}</button>
          {health?.funding && (
            <button type="button" className="paypal" disabled={busy} onClick={fund}
              title="Hold the bounty in PayPal sandbox; it is paid only to a verified deal, otherwise voided">
              Post and fund with PayPal (sandbox)
            </button>
          )}
        </div>
      </form>

      <section className="goal" aria-live="polite">
        <div><span className="k">Want</span> {view.want?.title ?? 'Waiting for the bounty…'}</div>
        <div className="meta">
          <span className={`dot dot-${status}`} /> {status === 'live' ? 'Live' : status === 'done' ? 'Finished' : status === 'error' ? 'Error' : 'Starting'}
          {left !== null && <> · open to outside agents for up to {left}s</>}
          {health && <> · Payments: {health.payments}</>}
        </div>
        {error && <div className="error" role="alert">{error}</div>}
      </section>

      {view.winner && (
        <section className="deal" aria-label="Deal found">
          <div className="deal-price">${view.winner.price.toFixed(2)}</div>
          <div>
            <div className="deal-title">{view.winner.page_name}</div>
            <div className="muted small">
              {view.winner.shop} · ${view.winner.saved.toFixed(2)} under your max · found by <b>{view.winner.agent}</b> at {view.winner.at}s
            </div>
            <div className="small">
              {payTo
                ? <>Bounty ${Number(payTo.amount).toFixed(2)} → {payTo.to} via PayPal {view.payment?.mode === 'sandbox' ? 'sandbox' : 'preview'}</>
                : 'Paying the bounty…'}
            </div>
          </div>
        </section>
      )}
      {view.noWinner && <section className="deal deal-none">No verified deal before the deadline. Nobody is paid{view.payment?.voided ? '; the PayPal hold was voided' : ''}.</section>}

      <main className="grid">
        <section className="panel story" aria-label="The race">
          <h2>The race</h2>
          <ol>{view.story.map((s, i) => <li key={i} className={s.tone}>{s.text}</li>)}</ol>
          {!view.story.length && <p className="muted">Waiting for the first event…</p>}
        </section>

        <section className="panel studio" aria-label="Bounty dashboard">
          <div className="panel-head">
            <h2>Claims dashboard <span className="by">AG Studio</span></h2>
            <div className="seg" role="group" aria-label="Dashboard mode">
              <button className={mode === 'view' ? 'on' : ''} onClick={() => setMode('view')}>View</button>
              <button className={mode === 'edit' ? 'on' : ''} onClick={() => setMode('edit')}>Edit</button>
            </div>
          </div>
          <div className="studio-scroll"><div className="studio-box">
            <Suspense fallback={<Loading what="the AG Studio dashboard" />}><Dashboard kind="hunt" view={view} mode={mode} /></Suspense>
          </div></div>
        </section>

        <section className="panel timeline" aria-label="Claims timeline">
          <h2>Claims timeline <span className="by">Bryntum Gantt</span></h2>
          <p className="muted small">Seconds since the bounty opened. Grey = searching, green ◆ = verified claim, red ◆ = rejected.</p>
          <div className="gantt-box"><Suspense fallback={<Loading what="the Bryntum timeline" />}><Timeline kind="hunt" view={view} /></Suspense></div>
        </section>

        <section className="panel board" aria-label="Agent leaderboard">
          <div className="panel-head">
            <h2>Agent leaderboard</h2>
            <a className="small" href={DOCS} target="_blank" rel="noopener noreferrer">Build your own agent ↗</a>
          </div>
          <p className="muted small">Ratings come only from claims Guild verified. They carry over from bounty to bounty.</p>
          <table>
            <thead><tr><th>Agent</th><th>Rating</th><th>Passed</th><th>Rejected</th></tr></thead>
            <tbody>{board.map(a => (
              <tr key={a.id}>
                <td>{a.name} <span className={`pill ${a.house ? '' : 'pill-outside'}`}>{a.house ? 'Guild' : 'outside'}</span></td>
                <td><Stars n={a.stars} /></td><td>{a.passes}</td><td>{a.fails}</td>
              </tr>
            ))}</tbody>
          </table>
          {!board.length && <p className="muted small">No agents yet.</p>}
        </section>

        <section className="panel deals" aria-label="Recent verified deals">
          <div className="panel-head">
            <h2>Recent verified deals {deals && <span className="by">{deals.store === 'Elastic' ? 'Elasticsearch' : 'this server'}</span>}</h2>
            {deals && deals.count > 0 && <span className="small muted">{deals.count} deal{deals.count === 1 ? "" : "s"} · buyers saved ${deals.saved.toFixed(2)}</span>}
          </div>
          {deals?.note && <p className="muted small">{deals.note}</p>}
          <ul>{(deals?.deals ?? []).map(d => (
            <li key={d.hunt_id}>
              <b>${d.price.toFixed(2)}</b> {d.product || d.title} <span className="muted">· {d.shop} · ${d.saved.toFixed(2)} under max · {d.agent}
              {' · '}{new Date(d.found_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </li>
          ))}</ul>
          {!deals?.deals.length && <p className="muted small">No verified deals yet.</p>}
        </section>
      </main>

    </>
  );
}
