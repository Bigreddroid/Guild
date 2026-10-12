// Turns a Guild Pay wallet's event stream into what the owner's page shows: the rules,
// the money, every purchase the agent asked for with the wallet's decision, and what
// the agent said along the way. Pure functions only.

export type WalletEvent = { type: string; [key: string]: any };

export interface Purchase {
  id: string; url: string; item: string; price: number; status: string; reasons: string[];
  checks: Record<string, boolean>; seller: string; order_id: string; at: number; shop: string; page_price: number | null;
}
export interface Totals { budget: number; spent: number; reserved: number; remaining: number }
export interface Line { text: string; tone: '' | 'good' | 'bad' | 'wait' | 'agent' }
export interface WalletView {
  id: string; agent: string; mode: string; want: string; mustHave: string[]; mustNot: string[];
  maxPerPurchase: number; approveAbove: number; totals: Totals; purchases: Purchase[];
  story: Line[]; closed: boolean; mandate: boolean; checkoutUrl: string; refundedAmount: number;
}

const money = (n: number) => `$${n.toFixed(2)}`;
const num = (v: any) => Number(v ?? 0);

export function shopOf(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname === 'demo-shops.guild.example' ? `${u.pathname.split('/')[1]} (demo)` : u.hostname.replace(/^www\./, '');
  } catch {
    return url.slice(0, 40);
  }
}

export function emptyWallet(): WalletView {
  return { id: '', agent: '', mode: '', want: '', mustHave: [], mustNot: [], maxPerPurchase: 0, approveAbove: 0,
           totals: { budget: 0, spent: 0, reserved: 0, remaining: 0 }, purchases: [], story: [], closed: false,
           mandate: false, checkoutUrl: '', refundedAmount: 0 };
}

export const CHECK_NAMES: Record<string, string> = {
  seller: 'seller listed', name: 'right item', currency: 'currency', max_price: 'under max', claimed_price: 'price matches',
  sanity: 'plausible price', stock: 'in stock', shipping: 'ships to you',
};

export function reduceWallet(events: WalletEvent[]): WalletView {
  const v = emptyWallet();
  const byId = new Map<string, Purchase>();
  const say = (text: string, tone: Line['tone'] = '') => v.story.push({ text, tone });
  const totals = (e: WalletEvent) => {
    if (e.budget !== undefined) v.totals = { budget: num(e.budget), spent: num(e.spent), reserved: num(e.reserved), remaining: num(e.remaining) };
  };
  const upsert = (p: any, extra: Partial<Purchase> = {}): Purchase => {
    const old = byId.get(p.id);
    const row: Purchase = {
      id: p.id, url: p.url, item: p.item, price: num(p.price), status: p.status, reasons: p.reasons ?? [],
      checks: p.checks ?? {}, seller: p.seller ?? '', order_id: p.order_id ?? '', shop: shopOf(p.url),
      at: old?.at ?? 0, page_price: old?.page_price ?? null, ...extra,
    };
    if (!old) v.purchases.push(row);
    else Object.assign(old, row);
    byId.set(p.id, old ?? row);
    return old ?? row;
  };

  for (const e of events) {
    totals(e);
    switch (e.type) {
      case 'wallet':
        Object.assign(v, { id: e.wallet_id, agent: e.agent, mode: e.mode, want: e.want, mustHave: e.must_have ?? [],
                           mustNot: e.must_not ?? [], maxPerPurchase: num(e.max_per_purchase), approveAbove: num(e.approve_above) });
        say(`Wallet opened: ${money(v.totals.budget)} for ${e.agent === 'shopperbot' ? 'ShopperBot' : e.agent}. `
            + `Up to ${money(v.maxPerPurchase)} per purchase; you approve anything over ${money(v.approveAbove)}.`);
        break;
      case 'agent':
        say(`${e.agent}${e.mode === 'scripted' ? ' (scripted)' : ''}: ${e.say}`, 'agent');
        break;
      case 'purchase': {
        const p = upsert(e.purchase, { at: num(e.at), page_price: e.page_price == null ? null : num(e.page_price) });
        const head = `${money(p.price)} · ${p.item} at ${p.shop}`;
        if (p.status === 'blocked') say(`✗ Blocked ${head}: ${p.reasons.join('; ')}`, 'bad');
        else if (p.status === 'pending') say(`‖ Waiting for you: ${head}. Every check passed.`, 'wait');
        else say(`✓ Approved ${head}: every check passed and it's within your rules.`, 'good');
        break;
      }
      case 'approved':
        upsert(e.purchase);
        say(`You approved ${money(num(e.purchase.price))} for ${e.purchase.item}.`, 'good');
        break;
      case 'rejected':
        upsert(e.purchase);
        say(`You rejected ${e.purchase.item}. Its ${money(num(e.purchase.price))} is free again.`);
        break;
      case 'paid': {
        const p = upsert(e.purchase);
        v.checkoutUrl = '';
        say(e.mode === 'preview'
          ? `PayPal preview (no keys): would pay ${money(p.price)} to ${p.seller || p.shop}.`
          : `PayPal sandbox: paid ${money(p.price)} to ${p.seller || p.shop}${e.via === 'mandate' ? ' from your saved PayPal' : ''} · order ${p.order_id}.`, 'good');
        break;
      }
      case 'checkout':
        upsert(e.purchase);
        v.checkoutUrl = e.approve_url ?? '';
        say('Approve this payment in PayPal to send it straight to the seller.', 'wait');
        break;
      case 'failed': {
        const p = upsert(e.purchase);
        say(`Payment failed for ${p.item}: ${p.reasons[0] ?? e.error ?? 'unknown error'}. Nothing was charged.`, 'bad');
        break;
      }
      case 'refunded': {
        const p = upsert(e.purchase);
        v.refundedAmount += p.price;
        say(`Refunded ${money(p.price)} to you.`, 'good');
        break;
      }
      case 'refund_failed':
        say(`Refund failed: ${e.error}.`, 'bad');
        break;
      case 'mandate':
        if (e.approved) { v.mandate = true; say('Your PayPal is connected: approved purchases pay without another login.', 'good'); }
        else if (e.revoked) { v.mandate = false; say('Your PayPal permission was revoked.'); }
        break;
      case 'stopped':
        say(`You pressed stop. ${e.dropped?.length ? `${e.dropped.length} waiting purchase(s) cancelled. ` : ''}No new purchases.`, 'bad');
        break;
      case 'closed':
        v.closed = true;
        say(`Wallet closed (${e.reason}). Spent ${money(v.totals.spent)} of ${money(v.totals.budget)}.`);
        break;
    }
  }
  return v;
}
