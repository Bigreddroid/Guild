// Turns a deal bounty's event stream into what the page shows: the story, the claims
// table, the agents racing, the winner and the PayPal result. Pure functions only,
// so the same events always give the same picture.

export type HuntEvent = { type: string; [key: string]: any };

export interface ClaimRow {
  n: number; agent: string; agent_id: string; outside: boolean; shop: string; url: string;
  claimed: number; page_price: number | null; result: 'passed' | 'rejected'; reason: string; at: number;
  stars_after: number; passed: number; rejected: number;
}
export interface RacerRow {
  agent_id: string; agent: string; tool: string; mode: string; outside: boolean;
  stars_before: number; stars: number; max_claims: number;
  searched_at: number | null; claims: number; passed: number; rejected: number; refused: number; error: string;
}
export interface Want {
  title: string; must_have: string[]; must_not: string[]; max_price: number; currency: string;
  ship_to: string; bounty: number; deadline_s: number;
}
export interface Winner { agent: string; agent_id: string; url: string; shop: string; price: number; saved: number; at: number; page_name: string }

export interface HuntView {
  want: Want | null; claims: ClaimRow[]; racers: RacerRow[]; winner: Winner | null;
  noWinner: string; done: boolean; payment: HuntEvent | null; story: Line[]; error: string;
}
export interface Line { text: string; tone: '' | 'good' | 'bad' }

export const HOUSE_NAMES: Record<string, string> = { scout: 'Scout', browser: 'Browser', quickfind: 'QuickFind' };
export const agentName = (id: string) => HOUSE_NAMES[id] ?? id.replace(/-[0-9a-f]{6}$/, '');
const money = (n: number) => `$${n.toFixed(2)}`;

export function shopOf(url: string): string {
  try {
    const u = new URL(url);
    // The demo shops all live on one labelled host; the first path part is the shop.
    return u.hostname === 'demo-shops.guild.example' ? `${u.pathname.split('/')[1]} (demo)` : u.hostname.replace(/^www\./, '');
  } catch {
    return url.slice(0, 40) || '(no link)';
  }
}

export function emptyHunt(): HuntView {
  return { want: null, claims: [], racers: [], winner: null, noWinner: '', done: false, payment: null, story: [], error: '' };
}

export function reduceHunt(events: HuntEvent[]): HuntView {
  const v = emptyHunt();
  const racers = new Map<string, RacerRow>();
  const racer = (id: string, outside = false): RacerRow => {
    let r = racers.get(id);
    if (!r) {
      r = { agent_id: id, agent: agentName(id), tool: outside ? 'outside developer' : '', mode: outside ? 'live' : '',
            outside, stars_before: 0, stars: 0, max_claims: 1, searched_at: null, claims: 0, passed: 0, rejected: 0,
            refused: 0, error: '' };
      racers.set(id, r);
    }
    return r;
  };
  const say = (text: string, tone: Line['tone'] = '') => v.story.push({ text, tone });

  for (const e of events) {
    switch (e.type) {
      case 'want': {
        v.want = { title: e.title, must_have: e.must_have, must_not: e.must_not, max_price: Number(e.max_price),
                   currency: e.currency, ship_to: e.ship_to, bounty: Number(e.bounty), deadline_s: Number(e.deadline_s) };
        for (const a of e.agents ?? []) {
          const r = racer(a.id);
          Object.assign(r, { agent: a.name, tool: a.tool, mode: a.mode, stars_before: a.stars, stars: a.stars, max_claims: a.max_claims });
        }
        say(`Bounty posted: ${money(v.want.bounty)} for "${e.title}". ${(e.agents ?? []).length} Guild agents start hunting; outside agents can claim too.`);
        break;
      }
      case 'searched': {
        const r = racer(e.agent_id);
        r.searched_at = Number(e.at);
        say(`${r.agent} finished searching${r.mode === 'simulated' ? ' (demo data)' : ` with ${r.tool}`}: ${e.claims} claim${e.claims === 1 ? '' : 's'}.`);
        break;
      }
      case 'claim': {
        const r = racer(e.agent_id, !!e.outside);
        if (e.agent_name) r.agent = e.agent_name;
        if (r.stars_before === 0) r.stars_before = Number(e.stars_after);
        r.claims += 1;
        r.stars = Number(e.stars_after);
        if (e.valid) r.passed += 1; else r.rejected += 1;
        const reason = (e.reasons ?? []).join('; ');
        v.claims.push({
          n: v.claims.length + 1, agent: r.agent, agent_id: e.agent_id, outside: !!e.outside, shop: shopOf(e.url), url: e.url,
          claimed: Number(e.claimed), page_price: e.page_price == null ? null : Number(e.page_price),
          result: e.valid ? 'passed' : 'rejected', reason, at: Number(e.at), stars_after: Number(e.stars_after),
          passed: e.valid ? 1 : 0, rejected: e.valid ? 0 : 1,
        });
        say(e.valid
          ? `✓ ${r.agent}${e.outside ? ' (outside)' : ''}: ${money(Number(e.claimed))} at ${shopOf(e.url)}. Guild read the page: it checks out.`
          : `✗ ${r.agent}${e.outside ? ' (outside)' : ''}: ${money(Number(e.claimed))} at ${shopOf(e.url)}. Rejected: ${reason}. Rating now ${Number(e.stars_after).toFixed(1)}★.`,
          e.valid ? 'good' : 'bad');
        break;
      }
      case 'claim_refused': {
        const r = racer(e.agent_id, true);
        if (e.agent_name) r.agent = e.agent_name;
        r.refused += 1;
        say(`${r.agent}: extra claim refused (${e.reason}).`);
        break;
      }
      case 'agent_error': {
        const r = racer(e.agent_id);
        r.error = e.error;
        say(`${r.agent} hit an error (${e.error}) and made no claims.`, 'bad');
        break;
      }
      case 'winner': {
        const price = Number(e.price);
        v.winner = { agent: e.agent_name ?? agentName(e.agent_id), agent_id: e.agent_id, url: e.url, shop: shopOf(e.url), price,
                     saved: Number(e.saved), at: Number(e.at), page_name: e.page_name };
        say(`Winner: ${v.winner.agent}, ${money(price)} at ${v.winner.shop}, ${money(Number(e.saved))} under your max. The ${money(Number(e.bounty))} bounty goes to its developer.`, 'good');
        break;
      }
      case 'recorded':
        say(e.store === 'Elastic' ? 'Deal saved to the verified-deal history in Elasticsearch.' : `Deal saved to this server's memory (${e.note ?? 'no Elastic keys'}).`);
        break;
      case 'alert':
        say(e.sent ? '"Deal found" alert sent through Zapier.' : `Zapier alert not sent: ${e.note}.`);
        break;
      case 'notice':
        say(e.message);
        break;
      case 'no_winner':
        v.noWinner = e.reason;
        say(`No winner: ${e.reason}. Nobody is paid.`, 'bad');
        break;
      case 'done':
        v.done = true;
        say(`Checked ${e.claims} claim${e.claims === 1 ? '' : 's'}; ${e.valid} passed.`);
        break;
      case 'error':
        v.done = true; v.error = e.message;
        say(`Bounty stopped: ${e.message}. No money moved.`, 'bad');
        break;
      case 'payment': {
        v.payment = e;
        const pays = (e.payouts ?? []).map((p: any) => `${p.to} ${money(Number(p.amount))}`).join(', ');
        if (e.mode === 'error') say(`PayPal: ${e.message}${e.debug_id ? ` (debug id ${e.debug_id})` : ''}.`, 'bad');
        else if (e.voided) say(`PayPal sandbox: no winner, so the hold was voided. ${money(Number(e.released))} back to the buyer.`);
        else if (e.mode === 'sandbox') say(`PayPal sandbox: captured ${money(Number(e.captured))}; payout ${e.payout_batch_id} to ${pays}.`, 'good');
        else say(pays ? `PayPal preview (no keys): would pay ${pays}.` : 'PayPal preview (no keys): nobody to pay, the hold would be voided.');
        break;
      }
    }
  }
  v.racers = [...racers.values()].sort((a, b) => b.passed - a.passed || b.stars - a.stars || a.agent.localeCompare(b.agent));
  return v;
}
