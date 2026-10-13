import { type ReactNode, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from './icons';
import Insights from './Insights';
import Renewals from './Renewals';
import { CHECK_NAMES, agentName, emptyWallet, money, reduceWallet, type Activity, type Purchase,
         type WalletEvent, type WalletView } from './wallet';

const Statement = lazy(() => import('./Statement'));

const EVENTS = ['wallet', 'agent', 'purchase', 'promotion', 'approved', 'rejected', 'paid', 'held', 'proof', 'voided',
                'failed', 'capture_failed', 'topped_up', 'withdrawn', 'withdraw_failed', 'limits', 'renewal', 'stopped',
                'closed'];
const MCP = 'https://github.com/Bigreddroid/Guild/tree/main/examples/mcp_server';
const AGENTS_GUIDE = 'https://github.com/Bigreddroid/Guild/blob/main/docs/AGENTS.md';

interface Form { must_have: string; must_not: string; max_price: string; ship_to: string; budget: string;
                 max_per_purchase: string; approve_above: string; marketing: string; tools: string }
const DEMO: Form = { must_have: 'Sony, WH-1000XM5', must_not: 'refurbished, renewed, used, case', max_price: '330',
                     ship_to: 'US', budget: '400', max_per_purchase: '350', approve_above: '250', marketing: '60',
                     tools: '40' };

// The form's marketing and tools amounts become their own spending limits; shopping keeps the rest.
function walletBody(f: Form): Record<string, unknown> {
  const { marketing, tools, ...rest } = f;
  const side = { marketing: Number(marketing), tools: Number(tools) };
  const named = Object.entries(side).filter(([, v]) => v > 0);
  if (!named.length) return rest;
  const shopping = Number(f.budget) - named.reduce((sum, [, v]) => sum + v, 0);
  const most = Math.min(Number(f.max_per_purchase), shopping);
  return { ...rest, envelopes: {
    ...(shopping > 0 ? { shopping: { limit: shopping.toFixed(2), max_per_purchase: most.toFixed(2),
                                     approve_above: Math.min(Number(f.approve_above), most).toFixed(2) } } : {}),
    ...Object.fromEntries(named.map(([k, v]) => [k, { limit: v.toFixed(2) }])) } };
}

const keyFor = (id: string) => `guildpay:key:${id}`;
const loadKey = (id: string) => { try { return sessionStorage.getItem(keyFor(id)) ?? ''; } catch { return ''; } };
const saveKey = (id: string, key: string) => { try { sessionStorage.setItem(keyFor(id), key); } catch { /* private mode */ } };

const STATUS: Record<string, string> = {
  paid: 'Paid', approved: 'Paying', blocked: 'Blocked', pending: 'Needs your OK', held: 'On hold', rejected: 'Declined',
  failed: 'Failed', voided: 'Released', refunded: 'Refunded', added: 'Added', returned: 'Returned',
};
const TONE_ICON: Record<string, string> = { in: 'in', out: 'out', blocked: 'shieldX', waiting: 'clock', held: 'clock', info: 'x' };

// ------------------------------------------------------------------ pieces

function Checks({ checks }: { checks: Record<string, boolean> }) {
  const entries = Object.entries(checks);
  if (!entries.length) return null;
  return (
    <ul className="gp-checks">
      {entries.map(([k, ok]) => (
        <li key={k} className={ok ? 'ok' : 'no'}>
          <Icon name={ok ? 'check' : 'x'} size={14} label={ok ? 'passed' : 'failed'} /> {CHECK_NAMES[k] ?? k}
        </li>
      ))}
    </ul>
  );
}

function Row({ a, open, toggle, children }: { a: Activity; open: boolean; toggle: () => void; children?: ReactNode }) {
  const p = a.purchase;
  const expandable = !!p;
  return (
    <li className={`gp-row tone-${a.tone}${open ? ' open' : ''}`}>
      <button className="gp-row-main" onClick={toggle} disabled={!expandable} aria-expanded={expandable ? open : undefined}>
        <span className="gp-avatar"><Icon name={a.purchase?.kind === 'promotion' && a.tone !== 'blocked' ? 'megaphone' : TONE_ICON[a.tone]} /></span>
        <span className="gp-row-text">
          <span className="gp-row-title">{a.title}</span>
          <span className="gp-row-sub">
            {a.detail}
            {p && p.category !== 'shopping' && <span className="gp-tag">{p.category}</span>}
          </span>
        </span>
        <span className="gp-row-amount">
          {a.amount !== null && <span className="gp-amt">{a.tone === 'in' ? '+' : ''}{money(a.amount)}</span>}
          <span className="gp-status">{STATUS[a.status] ?? a.status}</span>
        </span>
        {expandable && <span className="gp-chev"><Icon name="chevron" size={16} /></span>}
      </button>
      {open && p && <div className="gp-row-detail">{children}</div>}
    </li>
  );
}

function PurchaseDetail({ p }: { p: Purchase }) {
  return (
    <>
      <p className="gp-why">{p.reasons.join(' · ')}</p>
      <Checks checks={p.checks} />
      {p.kind === 'promotion' && p.proofs.length > 0 && (
        <ol className="gp-proofs">
          {p.proofs.map((pr, i) => (
            <li key={i} className={pr.valid ? 'ok' : 'no'}>
              <Icon name={pr.valid ? 'check' : 'x'} size={14} />
              <span>
                <span className="gp-proof-url">{pr.url.replace(/^https:\/\//, '')}</span>
                {!pr.valid && <span className="gp-proof-why">{pr.reasons.join('; ')}</span>}
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="gp-meta">
        {p.kind === 'buy' && <a href={p.url} target="_blank" rel="noopener noreferrer">Open the page <Icon name="link" size={13} /></a>}
        {p.status === 'paid' && p.order_id && <span>Paid to {p.seller || p.shop} · {p.order_id}</span>}
      </div>
    </>
  );
}

function BalanceCard({ v, owner, onTopUp, onWithdraw, onStop, busy }: {
  v: WalletView; owner: boolean; busy: boolean; onTopUp: (amount: string) => void; onWithdraw: () => void; onStop: () => void;
}) {
  const t = v.totals;
  const [adding, setAdding] = useState(false);
  const [typed, setTyped] = useState('');
  const room = Math.max(0, t.budget - t.balance);
  const amount = typed || String(room);
  const empty = !!v.id && t.funded === 0 && t.spent === 0 && !v.closed;
  const open = adding || empty;
  const pct = (n: number) => `${t.funded > 0 ? Math.min(100, (n / t.funded) * 100) : 0}%`;
  const live = !v.closed && owner && !!v.id;
  return (
    <section className="gp-balance" aria-label="Balance">
      <div className="gp-balance-top">
        <span className="gp-label">Available to your AI</span>
        <span className="gp-mode">{v.mode === 'sandbox' ? 'PayPal sandbox' : v.mode ? 'Demo money' : ''}</span>
      </div>
      <div className="gp-big" aria-live="polite">{v.id ? money(t.remaining) : '—'}</div>
      <div className="gp-bar" aria-hidden>
        <span className="b-spent" style={{ width: pct(t.spent) }} />
        <span className="b-hold" style={{ width: pct(t.reserved) }} />
      </div>
      <dl className="gp-stats">
        <div><dt>Loaded</dt><dd>{money(t.funded)}</dd></div>
        <div><dt><i className="sw sw-spent" />Spent</dt><dd>{money(t.spent)}</dd></div>
        <div><dt><i className="sw sw-hold" />On hold</dt><dd>{money(t.reserved)}</dd></div>
      </dl>
      {live && (
        <div className="gp-actions">
          {!empty && (
            <button className="gp-btn gp-btn-paypal" onClick={() => setAdding(a => !a)} aria-expanded={open} disabled={room <= 0}>
              <Icon name="plus" /> Add money
            </button>
          )}
          <button className="gp-btn gp-btn-glass" onClick={onWithdraw} disabled={busy || t.remaining <= 0}>
            <Icon name="undo" /> Return unspent
          </button>
          <button className="gp-btn gp-btn-glass" onClick={onStop} disabled={busy}>
            <Icon name="lock" /> Stop wallet
          </button>
        </div>
      )}
      {live && open && (
        <form className="gp-topup" onSubmit={e => { e.preventDefault(); onTopUp(amount); setAdding(false); setTyped(''); }}>
          {empty && <p className="gp-topup-lead"><b>Load your wallet to start.</b> Your AI can only spend what you put in, and anything unspent comes back to you.</p>}
          <div className="gp-chips" role="group" aria-label="Amount">
            {[...new Set([50, 100, Math.floor(room)])].filter(n => n > 0 && n <= room).map(n => (
              <button type="button" key={n} className={amount === String(n) ? 'on' : ''} onClick={() => setTyped(String(n))}>${n}</button>
            ))}
            <label className="gp-custom"><span className="sr">Amount</span>$
              <input inputMode="decimal" value={amount} onChange={e => setTyped(e.target.value)} aria-label="Amount in dollars" />
            </label>
          </div>
          <button type="submit" className="gp-btn gp-btn-paypal" disabled={busy || !(Number(amount) > 0) || Number(amount) > room}>
            {v.mode === 'sandbox' ? 'Pay with PayPal' : 'Add demo money'}
          </button>
          <p className="gp-hint">The wallet holds up to {money(t.budget)}; you can add {money(room)}. Your AI can never spend more than what's loaded.</p>
          {v.mode === 'preview' && (
            <p className="gp-hint">Demo money: this server has no PayPal sandbox keys yet, so nothing real moves.{' '}
              <a href="/api/readiness" target="_blank" rel="noopener noreferrer">What it needs to go live</a></p>
          )}
        </form>
      )}
      {v.closed && <p className="gp-closed">This wallet is closed. Anything unspent went back to you.</p>}
    </section>
  );
}

// ------------------------------------------------------------------ page

export default function Wallet() {
  const [form, setForm] = useState<Form>(DEMO);
  const [events, setEvents] = useState<WalletEvent[]>([]);
  const [walletId, setWalletId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [openRow, setOpenRow] = useState('');
  const [filter, setFilter] = useState<'all' | 'spent' | 'blocked' | 'money'>('all');
  const [demo, setDemo] = useState<any>(null);
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
    setBusy(true); setError(''); setOpenRow('');
    try {
      const r = await fetch('/api/wallets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(walletBody(form)) });
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

  const call = async (method: string, path: string, body?: unknown) => {
    setError(''); setBusy(true);
    try {
      const r = await fetch(path, { method, headers: { 'X-Owner-Key': ownerKey, 'Content-Type': 'application/json' },
                                    body: body === undefined ? undefined : JSON.stringify(body) });
      const out = await r.json().catch(() => ({}));
      if (!r.ok) setError(typeof out.detail === 'string' ? out.detail : `HTTP ${r.status}`);
      return r.ok ? out : null;
    } finally {
      setBusy(false);
    }
  };
  const act = (pid: string, action: string) => call('POST', `/api/wallets/${walletId}/purchases/${pid}/${action}`);
  const topUp = async (amount: string) => {
    const out = await call('POST', `/api/wallets/${walletId}/top-up`, { amount });
    if (out?.approve_url) window.location.assign(out.approve_url);
  };
  const stop = () => {
    if (window.confirm('Stop this wallet? Your AI can\'t spend any more, and everything unspent goes back to you.')) {
      call('POST', `/api/wallets/${walletId}/stop`);
    }
  };
  const bookDemo = async () => {
    if (!demo) return;
    const { proofs: _p, demo: _d, ...brief } = demo;
    const out = await call('POST', `/api/wallets/${walletId}/promotions`, brief);
    if (out?.id) setOpenRow(out.id);
  };
  const sendProof = (pid: string, url: string) => { setOpenRow(pid); call('POST', `/api/wallets/${walletId}/promotions/${pid}/proof`, { url }); };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const id = params.get('wallet');
    const err = params.get('wallet_error');
    if (id || err) window.history.replaceState(null, '', '/');
    if (id && /^[0-9a-f]{12}$/.test(id)) attach(id);   // back from PayPal
    else start();
    if (err) setError(err === 'cancelled' ? 'PayPal was cancelled; nothing was charged.' : err);
    fetch('/api/demo/promotion').then(r => r.json()).then(setDemo).catch(() => { /* demo extras only */ });
    return () => source.current?.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const owner = !!ownerKey;
  const waiting = view.purchases.filter(p => p.status === 'pending');
  const promo = [...view.purchases].reverse().find(p => p.kind === 'promotion');
  const shown = view.activity.filter(a => filter === 'all' || (filter === 'spent' && a.status === 'paid')
    || (filter === 'blocked' && a.tone === 'blocked') || (filter === 'money' && (a.tone === 'in' || a.status === 'returned')));
  const blockedTotal = view.purchases.filter(p => p.status === 'blocked').reduce((s, p) => s + p.price, 0);
  const field = (k: keyof Form, label: string, props: Record<string, any> = {}) => (
    <label className={`f-${k}`}>{label}
      <input value={form[k]} onChange={e => setForm({ ...form, [k]: e.target.value })} {...props} />
    </label>
  );

  return (
    <div className="gp">
      <div className="gp-main">
        <BalanceCard v={view} owner={owner} busy={busy} onTopUp={topUp} onWithdraw={() => call('POST', `/api/wallets/${walletId}/withdraw`)}
          onStop={stop} />
        {(error || view.error) && <div className="gp-alert" role="alert">{error || view.error}</div>}

        {owner && waiting.length > 0 && (
          <section className="gp-card gp-ask" aria-label="Needs your OK">
            <h2>Needs your OK</h2>
            {waiting.map(p => (
              <div className="gp-ask-row" key={p.id}>
                <div className="gp-ask-text">
                  <b>{p.kind === 'promotion' ? `Promotion by ${p.creator}` : p.item}</b>
                  <span>{p.shop} · {p.kind === 'promotion' ? 'booked by your AI' : `every check passed, above your ${money(view.approveAbove)} limit`}</span>
                </div>
                <div className="gp-ask-amt">{money(p.price)}</div>
                <div className="gp-ask-btns">
                  <button className="gp-btn gp-btn-primary" disabled={busy} onClick={() => act(p.id, 'approve')}>Approve</button>
                  <button className="gp-btn gp-btn-quiet" disabled={busy} onClick={() => act(p.id, 'reject')}>Decline</button>
                </div>
              </div>
            ))}
          </section>
        )}

        <section className="gp-card" aria-label="Activity">
          <div className="gp-card-head">
            <h2>Activity</h2>
            <div className="gp-filter" role="group" aria-label="Show">
              {(['all', 'spent', 'blocked', 'money'] as const).map(f => (
                <button key={f} className={filter === f ? 'on' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>
                  {{ all: 'All', spent: 'Paid', blocked: 'Blocked', money: 'Money in/out' }[f]}
                </button>
              ))}
            </div>
          </div>
          {blockedTotal > 0 && (
            <p className="gp-saved"><Icon name="shieldCheck" size={16} /> Guild stopped {money(blockedTotal)} of bad spending before any money moved.</p>
          )}
          <ul className="gp-list">
            {shown.map(a => (
              <Row key={a.key} a={a} open={openRow === a.key} toggle={() => setOpenRow(openRow === a.key ? '' : a.key)}>
                {a.purchase && <PurchaseDetail p={a.purchase} />}
                {owner && a.purchase?.status === 'held' && (
                  <div className="gp-row-btns">
                    <button className="gp-btn gp-btn-quiet" disabled={busy} onClick={() => act(a.purchase!.id, 'cancel')}>Cancel and release</button>
                  </div>
                )}
              </Row>
            ))}
          </ul>
          {!shown.length && <p className="gp-empty">{view.id ? 'Nothing here yet.' : 'Opening your wallet…'}</p>}
        </section>
      </div>

      <aside className="gp-side">
        {owner && view.id && view.totals.funded + view.totals.withdrawn > 0 && (
          <Insights walletId={walletId} ownerKey={ownerKey} version={events.length} busy={busy}
            apply={limits => call('POST', `/api/wallets/${walletId}/limits`, { limits })} />
        )}
        <section className="gp-card" aria-label="Your AI">
          <div className="gp-agent">
            <span className="gp-avatar gp-avatar-ai"><Icon name="bot" /></span>
            <div>
              <h2>{agentName(view.agent) || 'Your AI'}</h2>
              <p className="gp-sub">{view.agentMode === 'scripted' ? 'Scripted demo agent' : view.agentMode === 'live' ? 'Claude, live'
                : view.totals.funded === 0 && view.agent === 'shopperbot' ? 'Starts shopping once the wallet has money' : 'Shops with this wallet'}</p>
            </div>
          </div>
          {view.notes.length > 0 && (
            <ul className="gp-notes" aria-label="What your AI said">
              {view.notes.slice(-3).map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
          <p className="gp-small">Any AI agent can use a wallet like this, through <a href={MCP} target="_blank" rel="noopener noreferrer">MCP</a> or
            the <a href={AGENTS_GUIDE} target="_blank" rel="noopener noreferrer">REST API</a>. It can only ask; Guild checks, then pays from the loaded money.</p>
        </section>

        {view.id && (
          <Renewals renewals={view.renewals} owner={owner} busy={busy} closed={view.closed}
            canStart={view.totals.remaining > 0}
            start={() => fetch('/api/demo/renewal').then(r => r.json())
              .then(({ demo: _d, ...body }) => call('POST', `/api/wallets/${walletId}/renewals`, body))}
            act={(id, action) => call('POST', `/api/wallets/${walletId}/renewals/${id}/${action}`)} />
        )}

        <section className="gp-card" aria-label="Pay a creator on proof">
          <div className="gp-card-head"><h2>Pay a creator on proof</h2><Icon name="megaphone" /></div>
          <p className="gp-small">Book a creator for a post. The money is held in your wallet and paid only when the post is live,
            on their channel, links to you and says it's an ad.</p>
          {!promo && owner && !view.closed && (
            <button className="gp-btn gp-btn-outline" disabled={busy || !view.id || !demo} onClick={bookDemo}>Book the demo creator · $40</button>
          )}
          {promo && (
            <div className="gp-promo">
              <div className="gp-promo-head"><b>{promo.creator}</b><span className={`gp-pill pill-${promo.status}`}>{STATUS[promo.status] ?? promo.status}</span></div>
              {promo.status === 'held' && demo && (
                <>
                  <p className="gp-small">Try sending a post as proof:</p>
                  <div className="gp-proof-btns">
                    <button className="gp-btn gp-btn-quiet" disabled={busy} onClick={() => sendProof(promo.id, demo.proofs.copycat)}>Copied post</button>
                    <button className="gp-btn gp-btn-quiet" disabled={busy} onClick={() => sendProof(promo.id, demo.proofs.hidden_ad)}>Hidden #ad</button>
                    <button className="gp-btn gp-btn-quiet" disabled={busy} onClick={() => sendProof(promo.id, demo.proofs.no_link)}>No link</button>
                    <button className="gp-btn gp-btn-primary" disabled={busy} onClick={() => sendProof(promo.id, demo.proofs.good)}>The real post</button>
                  </div>
                </>
              )}
              {promo.proofs.length > 0 && (
                <p className={`gp-small ${promo.proofs[promo.proofs.length - 1].valid ? 'ok' : 'no'}`}>
                  Last proof: {promo.proofs[promo.proofs.length - 1].reasons.join('; ')}
                </p>
              )}
            </div>
          )}
        </section>

        <section className="gp-card" aria-label="Your rules">
          <div className="gp-card-head"><h2>Your rules</h2><Icon name="rules" /></div>
          <dl className="gp-rules">
            <div><dt>Buy</dt><dd>{view.mustHave.join(', ') || '—'}</dd></div>
            <div><dt>Never</dt><dd>{view.mustNot.join(', ') || '—'}</dd></div>
            <div><dt>Most per purchase</dt><dd>{money(view.maxPerPurchase)}</dd></div>
            <div><dt>Ask me above</dt><dd>{money(view.approveAbove)}</dd></div>
            <div><dt>Wallet holds up to</dt><dd>{money(view.totals.budget)}</dd></div>
          </dl>
          {view.totals.envelopes.length > 1 && (
            <ul className="gp-limits" aria-label="Spending limits">
              {view.totals.envelopes.map(e => (
                <li key={e.name}>
                  <span className="gp-limit-name">{e.name}</span>
                  <span className="gp-limit-left">{money(e.remaining)} left of {money(e.limit)}</span>
                  <span className="gp-minibar" aria-hidden><span style={{ width: `${e.limit ? Math.min(100, ((e.spent + e.reserved) / e.limit) * 100) : 0}%` }} /></span>
                </li>
              ))}
            </ul>
          )}
          <details className="gp-new">
            <summary>New wallet with different rules</summary>
            <form className="gp-form" onSubmit={e => { e.preventDefault(); if (!busy) start(); }}>
              {field('must_have', 'Buy (name must include)')}
              {field('must_not', 'Never buy (words)')}
              {field('max_price', 'Max item price', { inputMode: 'decimal' })}
              {field('ship_to', 'Ship to (country)', { maxLength: 2 })}
              {field('budget', 'Wallet holds up to', { inputMode: 'decimal' })}
              {field('max_per_purchase', 'Most per purchase', { inputMode: 'decimal' })}
              {field('approve_above', 'Ask me above', { inputMode: 'decimal' })}
              {field('marketing', 'Of which for marketing', { inputMode: 'decimal' })}
              {field('tools', 'Of which for AI tools', { inputMode: 'decimal' })}
              <button type="submit" className="gp-btn gp-btn-primary" disabled={busy}>{busy ? 'Opening…' : 'Open new wallet'}</button>
            </form>
          </details>
        </section>
      </aside>
      {view.activity.length > 0 && (
        <div className="gp-wide">
          <Suspense fallback={<section className="gp-card"><p className="gp-empty">Loading the statement…</p></section>}>
            <Statement activity={view.activity} />
          </Suspense>
        </div>
      )}
    </div>
  );
}
