import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CHECK_NAMES, emptyWallet, reduceWallet, type Purchase, type WalletEvent } from './wallet';

const Dashboard = lazy(() => import('./Dashboard'));
const Timeline = lazy(() => import('./Timeline'));
const Loading = ({ what }: { what: string }) => <p className="muted small loading">Loading {what}…</p>;

const EVENTS = ['wallet', 'agent', 'purchase', 'approved', 'rejected', 'paid', 'checkout', 'failed', 'refunded',
                'refund_failed', 'mandate', 'stopped', 'closed'];
const MCP = 'https://github.com/Bigreddroid/Guild/tree/main/examples/mcp_server';

interface Form { must_have: string; must_not: string; max_price: string; ship_to: string; budget: string;
                 max_per_purchase: string; approve_above: string }
const DEMO: Form = { must_have: 'Sony, WH-1000XM5', must_not: 'refurbished, renewed, used, case', max_price: '330',
                     ship_to: 'US', budget: '400', max_per_purchase: '350', approve_above: '250' };

const keyFor = (id: string) => `guildpay:key:${id}`;
const loadKey = (id: string) => { try { return sessionStorage.getItem(keyFor(id)) ?? ''; } catch { return ''; } };
const saveKey = (id: string, key: string) => { try { sessionStorage.setItem(keyFor(id), key); } catch { /* private mode */ } };
const money = (n: number) => `$${n.toFixed(2)}`;
const ICON: Record<string, string> = { paid: '✓', approved: '✓', pending: '‖', blocked: '✗', rejected: '–', refunded: '↩', failed: '!' };

function PurchaseCard({ p, owner, act }: { p: Purchase; owner: boolean; act: (id: string, a: string) => void }) {
  return (
    <li className={`buy buy-${p.status}`}>
      <div className="buy-head">
        <span className="buy-icon" aria-hidden>{ICON[p.status] ?? '•'}</span>
        <b>{money(p.price)}</b> <span className="buy-item">{p.item}</span>
        <span className="muted"> · {p.shop}</span>
        <span className={`pill pill-${p.status}`}>{p.status === 'pending' ? 'waiting for you' : p.status}</span>
      </div>
      <div className="buy-why">{p.reasons.join('; ')}</div>
      {Object.keys(p.checks).length > 0 && (
        <div className="checks">{Object.entries(p.checks).map(([k, ok]) => (
          <span key={k} className={`check ${ok ? 'ok' : 'no'}`}>{ok ? '✓' : '✗'} {CHECK_NAMES[k] ?? k}</span>
        ))}</div>
      )}
      {p.status === 'paid' && p.seller && <div className="small muted">Paid to {p.seller}{p.order_id ? ` · ${p.order_id}` : ''}</div>}
      {owner && p.status === 'pending' && (
        <div className="buy-actions">
          <button className="ok" onClick={() => act(p.id, 'approve')}>Approve {money(p.price)}</button>
          <button className="ghost" onClick={() => act(p.id, 'reject')}>Reject</button>
        </div>
      )}
      {owner && p.status === 'paid' && (
        <div className="buy-actions"><button className="ghost" onClick={() => act(p.id, 'refund')}>Refund</button></div>
      )}
    </li>
  );
}

export default function Wallet() {
  const [form, setForm] = useState<Form>(DEMO);
  const [events, setEvents] = useState<WalletEvent[]>([]);
  const [walletId, setWalletId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const source = useRef<EventSource | null>(null);
  const view = useMemo(() => (events.length ? reduceWallet(events) : emptyWallet()), [events]);
  const ownerKey = walletId ? loadKey(walletId) : '';

  const attach = useCallback((id: string) => {
    source.current?.close();
    setWalletId(id);
    const es = new EventSource(`/api/runs/${encodeURIComponent(id)}/events?delay=0.4`);
    source.current = es;
    let first = true;
    const onEvent = (msg: MessageEvent) => {
      const event = JSON.parse(msg.data);
      const replace = first;  // read now: React may run the updater after `first` flips
      first = false;
      setEvents(prev => (replace ? [event] : [...prev, event]));
    };
    for (const t of EVENTS) es.addEventListener(t, onEvent as EventListener);
    es.addEventListener('end', () => es.close());
  }, []);

  const start = async () => {
    setBusy(true); setError('');
    try {
      const r = await fetch('/api/wallets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(form) });
      const body = await r.json();
      if (!r.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${r.status}`);
      saveKey(body.wallet_id, body.owner_key);
      attach(body.wallet_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const owner = async (method: string, path: string) => {
    setError('');
    const r = await fetch(path, { method, headers: { 'X-Owner-Key': ownerKey } });
    const body = await r.json().catch(() => ({}));
    if (!r.ok) setError(typeof body.detail === 'string' ? body.detail : `HTTP ${r.status}`);
    return body;
  };
  const act = (pid: string, action: string) => owner('POST', `/api/wallets/${walletId}/purchases/${pid}/${action}`);
  const connect = async () => {
    const body = await owner('POST', `/api/wallets/${walletId}/mandate`);
    if (body.approve_url) window.location.assign(body.approve_url);
    else if (body.mode === 'preview') setError('PayPal keys are not set on this server, so payments are a labelled preview.');
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('wallet');
    const err = params.get('wallet_error');
    if (id || err) window.history.replaceState(null, '', '/');
    if (id && /^[0-9a-f]{12}$/.test(id)) attach(id);   // back from PayPal
    else start();
    if (err) setError(err === 'cancelled' ? 'PayPal was cancelled; nothing was charged.' : err);
    return () => source.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const t = view.totals;
  const pct = (n: number) => `${t.budget ? Math.min(100, (n / t.budget) * 100) : 0}%`;
  const field = (k: keyof Form, label: string, props: Record<string, any> = {}) => (
    <label className={`f-${k}`}>{label}
      <input value={form[k]} onChange={e => setForm({ ...form, [k]: e.target.value })} {...props} />
    </label>
  );
  const waiting = view.purchases.filter(p => p.status === 'pending');

  return (
    <>
      <p className="tab-intro">Give an AI agent a budget it can't waste. Guild checks every purchase before PayPal pays:
        a real seller, the right item, the price the page really shows, inside your rules. Then the seller's own PayPal is paid.</p>

      <form className="want-form wallet-form" onSubmit={e => { e.preventDefault(); if (!busy) start(); }} aria-label="Set up a wallet">
        {field('must_have', 'Buy (name must include)')}
        {field('must_not', 'Never (must not include)')}
        {field('max_price', 'Max item price', { inputMode: 'decimal' })}
        {field('ship_to', 'Ship to', { maxLength: 2 })}
        {field('budget', 'Budget (USD)', { inputMode: 'decimal' })}
        {field('max_per_purchase', 'Max per purchase', { inputMode: 'decimal' })}
        {field('approve_above', 'Ask me above', { inputMode: 'decimal' })}
        <div className="actions"><button type="submit" disabled={busy}>{busy ? 'Opening…' : 'Give ShopperBot this wallet'}</button></div>
      </form>

      <section className="goal wallet-bar" aria-live="polite">
        <div className="wallet-top">
          <div><span className="k">Wallet</span> {view.want || 'Opening…'}</div>
          <div className="wallet-actions">
            {view.mode === 'sandbox' && !view.mandate && !view.closed && ownerKey &&
              <button className="paypal" onClick={connect}>Connect PayPal (sandbox)</button>}
            {view.mandate && <span className="pill pill-paid">PayPal connected</span>}
            {view.checkoutUrl && <a className="paypal-link" href={view.checkoutUrl}>Approve in PayPal ↗</a>}
            {!view.closed && ownerKey && view.id &&
              <button className="stop" onClick={() => owner('POST', `/api/wallets/${walletId}/stop`)}>Stop</button>}
          </div>
        </div>
        <div className="budget" role="img"
          aria-label={`Spent ${money(t.spent)}, waiting ${money(t.reserved)}, left ${money(t.remaining)} of ${money(t.budget)}`}>
          <span className="b-spent" style={{ width: pct(t.spent) }} />
          <span className="b-wait" style={{ width: pct(t.reserved) }} />
        </div>
        <div className="meta">
          <b>{money(t.spent)}</b> spent · <b>{money(t.reserved)}</b> waiting for you · <b>{money(t.remaining)}</b> left of {money(t.budget)}
          {view.mode && <> · payments: {view.mode === 'preview' ? 'preview (no PayPal keys)' : 'PayPal sandbox'}</>}
          {view.closed && <> · closed</>}
        </div>
        {error && <div className="error" role="alert">{error}</div>}
      </section>

      {waiting.length > 0 && ownerKey && (
        <section className="deal deal-wait" aria-label="Waiting for your approval">
          <div className="deal-price">{money(waiting[0].price)}</div>
          <div>
            <div className="deal-title">{waiting[0].item} at {waiting[0].shop}</div>
            <div className="muted small">Every check passed. It's above your {money(view.approveAbove)} ask-me amount.</div>
            <div className="buy-actions">
              <button className="ok" onClick={() => act(waiting[0].id, 'approve')}>Approve and pay the seller</button>
              <button className="ghost" onClick={() => act(waiting[0].id, 'reject')}>Reject</button>
            </div>
          </div>
        </section>
      )}

      <main className="grid">
        <section className="panel story" aria-label="What happened">
          <h2>What happened</h2>
          <ol>{view.story.map((s, i) => <li key={i} className={s.tone}>{s.text}</li>)}</ol>
          {!view.story.length && <p className="muted">Waiting for the first event…</p>}
        </section>

        <section className="panel purchases" aria-label="Purchases">
          <div className="panel-head">
            <h2>Purchases</h2>
            <a className="small" href={MCP} target="_blank" rel="noopener noreferrer">Use this wallet from any agent (MCP) ↗</a>
          </div>
          <ul className="buys">{view.purchases.map(p => <PurchaseCard key={p.id} p={p} owner={!!ownerKey} act={act} />)}</ul>
          {!view.purchases.length && <p className="muted small">No purchase requests yet.</p>}
        </section>

        <section className="panel studio" aria-label="Wallet dashboard">
          <div className="panel-head">
            <h2>Wallet dashboard <span className="by">AG Studio</span></h2>
            <div className="seg" role="group" aria-label="Dashboard mode">
              <button className={mode === 'view' ? 'on' : ''} onClick={() => setMode('view')}>View</button>
              <button className={mode === 'edit' ? 'on' : ''} onClick={() => setMode('edit')}>Edit</button>
            </div>
          </div>
          <div className="studio-scroll"><div className="studio-box studio-box-wallet">
            <Suspense fallback={<Loading what="the AG Studio dashboard" />}><Dashboard kind="wallet" view={view} mode={mode} /></Suspense>
          </div></div>
        </section>

        <section className="panel timeline" aria-label="Purchase timeline">
          <h2>Purchase timeline <span className="by">Bryntum Gantt</span></h2>
          <p className="muted small">Seconds since the wallet opened. Red ◆ blocked, amber ◆ waiting for you, green ◆ paid.</p>
          <div className="gantt-box"><Suspense fallback={<Loading what="the Bryntum timeline" />}><Timeline kind="wallet" view={view} /></Suspense></div>
        </section>
      </main>
    </>
  );
}
