// Turns a Guild Pay wallet's event stream into what the owner's page shows: the money,
// the rules, one activity feed (money in, money out, what was blocked) and what the
// agent said. Pure functions only.

export type WalletEvent = { type: string; [key: string]: any };

export interface Proof { url: string; valid: boolean; reasons: string[]; checks: Record<string, boolean> }
export interface Purchase {
  id: string; url: string; item: string; price: number; status: string; reasons: string[];
  checks: Record<string, boolean>; seller: string; order_id: string; at: number; shop: string; page_price: number | null;
  category: string; kind: 'buy' | 'promotion'; creator: string; channel: string; proofs: Proof[]; bookedBy: string;
}
export interface Envelope { name: string; limit: number; spent: number; reserved: number; remaining: number }
export interface Totals {
  budget: number; funded: number; balance: number; withdrawn: number; spent: number; reserved: number; remaining: number;
  envelopes: Envelope[];
}
export interface Renewal {
  id: string; tool: string; url: string; price: number; every_minutes: number; category: string; booked_by: string;
  status: string; reason: string; charges: number; next_in_s: number | null; last_used_s_ago: number | null; unused: boolean;
}
export type Tone = 'in' | 'out' | 'blocked' | 'waiting' | 'held' | 'info';
export interface Activity {
  key: string; tone: Tone; title: string; detail: string; amount: number | null; status: string; purchase?: Purchase;
  ref?: string;   // PayPal order or payout batch id
}
export interface WalletView {
  id: string; agent: string; mode: string; want: string; mustHave: string[]; mustNot: string[];
  maxPerPurchase: number; approveAbove: number; totals: Totals; purchases: Purchase[];
  activity: Activity[]; notes: string[]; agentMode: string; closed: boolean; stopped: boolean; error: string;
  renewals: Renewal[];
}

export const money = (n: number) => `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const num = (v: any) => Number(v ?? 0);

export function shopOf(url: string): string {
  try {
    const u = new URL(url);
    if (u.hostname === 'demo-shops.guild.example') {
      const [first, second] = u.pathname.split('/').filter(Boolean);
      return first === 'creators' ? `${second} (demo)` : `${first} (demo)`;
    }
    return u.hostname.replace(/^www\./, '');
  } catch {
    return url.slice(0, 40);
  }
}

export const agentName = (id: string) => (id === 'shopperbot' ? 'ShopperBot' : id);

export function emptyWallet(): WalletView {
  return { id: '', agent: '', mode: '', want: '', mustHave: [], mustNot: [], maxPerPurchase: 0, approveAbove: 0,
           totals: { budget: 0, funded: 0, balance: 0, withdrawn: 0, spent: 0, reserved: 0, remaining: 0, envelopes: [] },
           purchases: [], activity: [], notes: [], agentMode: '', closed: false, stopped: false, error: '', renewals: [] };
}

export const CHECK_NAMES: Record<string, string> = {
  seller: 'Real seller', name: 'Right item', currency: 'Currency', max_price: 'Under your max',
  claimed_price: 'Price matches page', sanity: 'Plausible price', stock: 'In stock', shipping: 'Ships to you',
  live: 'Post is live', channel: "Creator's own channel", link: 'Links to you', disclosure: 'Says it is an ad',
  mentions: 'Mentions you',
};

const TONE: Record<string, Tone> = { paid: 'out', approved: 'out', blocked: 'blocked', pending: 'waiting', held: 'held',
                                     rejected: 'info', failed: 'blocked', voided: 'info', refunded: 'info' };

const isMoneyIn = (e: WalletEvent) => e.type === 'topped_up' || (e.type === 'wallet' && e.mode === 'preview' && num(e.funded) > 0);
const isMoneyOut = (e: WalletEvent) => e.type === 'withdrawn' && num(e.amount) > 0;

export function reduceWallet(events: WalletEvent[]): WalletView {
  const v = emptyWallet();
  const byId = new Map<string, Purchase>();
  const feed: (Activity | string)[] = [];   // money lines, or purchase ids (resolved at the end)

  const totals = (e: WalletEvent) => {
    if (e.budget === undefined) return;
    v.totals = {
      budget: num(e.budget), funded: num(e.funded ?? e.budget), balance: num(e.balance ?? e.remaining),
      withdrawn: num(e.withdrawn), spent: num(e.spent), reserved: num(e.reserved), remaining: num(e.remaining),
      envelopes: (e.envelopes ?? []).map((x: any) => ({ name: String(x.name), limit: num(x.limit), spent: num(x.spent),
                                                        reserved: num(x.reserved), remaining: num(x.remaining) })),
    };
  };
  const upsert = (p: any, extra: Partial<Purchase> = {}): Purchase => {
    const old = byId.get(p.id);
    const row: Purchase = {
      id: p.id, url: p.url, item: p.item, price: num(p.price), status: p.status, reasons: p.reasons ?? [],
      checks: p.checks ?? {}, seller: p.seller ?? '', order_id: p.order_id ?? '', shop: shopOf(p.url),
      category: p.category ?? 'shopping', kind: p.kind === 'promotion' ? 'promotion' : 'buy',
      creator: p.creator ?? '', channel: p.channel ?? '', proofs: p.proofs ?? old?.proofs ?? [], bookedBy: p.booked_by ?? '',
      at: old?.at ?? 0, page_price: old?.page_price ?? null, ...extra,
    };
    if (!old) { v.purchases.push(row); feed.push(row.id); }
    else Object.assign(old, row);
    byId.set(p.id, old ?? row);
    return old ?? row;
  };

  events.forEach((e, i) => {
    totals(e);
    if (isMoneyIn(e)) {
      const demo = e.mode === 'preview';
      feed.push({ key: `in${i}`, tone: 'in', status: 'added', ref: e.order_id ?? '', amount: num(e.type === 'wallet' ? e.funded : e.amount),
                  title: demo ? 'Demo money added' : 'Added with PayPal',
                  detail: demo ? 'No PayPal keys on this server, so nothing real moved' : `PayPal order ${e.order_id ?? ''}` });
    }
    if (isMoneyOut(e)) {
      feed.push({ key: `out${i}`, tone: 'out', status: 'returned', amount: num(e.amount), title: 'Returned to you',
                  detail: e.mode === 'preview' ? 'Demo money' : 'Refunded to your PayPal' });
    }
    switch (e.type) {
      case 'wallet':
        Object.assign(v, { id: e.wallet_id, agent: e.agent, mode: e.mode, want: e.want, mustHave: e.must_have ?? [],
                           mustNot: e.must_not ?? [], maxPerPurchase: num(e.max_per_purchase), approveAbove: num(e.approve_above) });
        break;
      case 'agent':
        v.notes.push(String(e.say));
        v.agentMode = e.mode ?? '';
        break;
      case 'purchase': case 'promotion':
        upsert(e.purchase, { at: num(e.at), page_price: e.page_price == null ? null : num(e.page_price) });
        break;
      case 'approved': case 'rejected': case 'paid': case 'held': case 'voided': case 'failed': case 'refunded':
      case 'proof':
        if (e.purchase) upsert(e.purchase);
        break;
      case 'capture_failed':
        if (e.purchase) upsert(e.purchase);
        v.error = `Paying the creator failed (${e.error}). The money is still held.`;
        break;
      case 'withdraw_failed':
        v.error = `Returning your money failed (${e.error}).`;
        break;
      case 'renewal': {
        const r = e.renewal;
        const row: Renewal = { ...r, price: num(r.price) };
        const i = v.renewals.findIndex(x => x.id === r.id);
        if (i >= 0) v.renewals[i] = row; else v.renewals.push(row);
        break;
      }
      case 'stopped':
        v.stopped = true;
        break;
      case 'closed':
        v.closed = true;
        break;
    }
  });

  v.activity = feed.map(f => {
    if (typeof f !== 'string') return f;
    const p = byId.get(f)!;
    return { key: p.id, tone: TONE[p.status] ?? 'info', status: p.status, amount: p.price, purchase: p, ref: p.order_id,
             title: p.kind === 'promotion' ? `Promotion by ${p.creator}` : p.item,
             detail: p.kind === 'promotion' ? `${p.shop} · pays on proof` : p.shop };
  }).reverse();
  return v;
}
