// Turns the server's event stream into the tables the dashboard and timeline show.
// Pure functions only, so the same events always give the same picture.

export type GuildEvent = { type: string; [key: string]: any };

export interface BidRow {
  job: string; attempt: number; agent: string; price: number; eta_s: number;
  stars: number; value: number; status: 'won' | 'lost' | 'ineligible'; reason: string;
}
export interface SettlementRow {
  job: string; agent: string; verdict: string; outcome: string; price: number;
  paid: number; withheld: number; stars_after: number; reasons: string;
}
export interface AgentRow { agent: string; stars: number; earned: number; won: number; failed: number }
export interface JobPlan { id: string; title: string; skill: string; max_price: string; depends_on: string[] }

export interface Attempt {
  job: string; attempt: number; agent: string; price: number;
  start: number; end: number; // seconds from the start of the run (agent work time)
  outcome?: string; tool?: string; mode?: string;
}

export interface MarketView {
  goal: string; lead: string; reviewer: string;
  jobs: JobPlan[]; bids: BidRow[]; settlements: SettlementRow[]; agents: AgentRow[];
  attempts: Attempt[]; blocked: Record<string, string[]>; unfunded: Record<string, string>;
  budget: number; spent: number; done: boolean; story: string[];
}

export const AGENT_NAMES: Record<string, string> = {
  sourcer: 'Sourcer', researcher: 'Researcher', web_operator: 'Web Operator', doer: 'Doer', cheapbot: 'CheapBot',
};
export const agentName = (id: string) => AGENT_NAMES[id] ?? id;
const money = (n: number) => `$${n.toFixed(2)}`;

export function emptyView(budget: number): MarketView {
  return {
    goal: '', lead: '', reviewer: '', jobs: [], bids: [], settlements: [], agents: [], attempts: [],
    blocked: {}, unfunded: {}, budget, spent: 0, done: false, story: [],
  };
}

export function reduce(events: GuildEvent[], budget: number): MarketView {
  const v = emptyView(budget);
  const agents = new Map<string, AgentRow>();
  const lastBids = new Map<string, BidRow[]>();
  let clock = 0;
  const touch = (id: string, stars?: number) => {
    const row = agents.get(id) ?? { agent: agentName(id), stars: 0, earned: 0, won: 0, failed: 0 };
    if (stars !== undefined) row.stars = stars;
    agents.set(id, row);
    return row;
  };

  for (const e of events) {
    switch (e.type) {
      case 'plan':
        v.goal = e.goal; v.lead = e.lead; v.reviewer = e.reviewer; v.jobs = e.jobs;
        v.story.push(`Lead split the goal into ${e.jobs.length} jobs.`);
        break;
      case 'bids': {
        const rows: BidRow[] = e.bids.map((b: any, i: number) => ({
          job: e.job_id, attempt: e.attempt, agent: agentName(b.agent_id), price: Number(b.price),
          eta_s: b.eta_s, stars: b.stars, value: b.value,
          status: !b.eligible ? 'ineligible' : i === 0 ? 'won' : 'lost', reason: b.reason,
        }));
        e.bids.forEach((b: any) => touch(b.agent_id, b.stars));
        v.bids.push(...rows);
        lastBids.set(e.job_id, rows);
        const eligible = rows.filter(r => r.status !== 'ineligible').length;
        v.story.push(`${e.job_id}: ${rows.length} bid${rows.length === 1 ? '' : 's'} (${eligible} eligible)${e.attempt > 1 ? ', re-auction' : ''}.`);
        break;
      }
      case 'awarded': {
        const bid = (lastBids.get(e.job_id) ?? []).find(b => b.agent === agentName(e.agent_id));
        const eta = bid?.eta_s ?? 60;
        v.attempts.push({ job: e.job_id, attempt: bid?.attempt ?? 1, agent: e.agent_id, price: Number(e.price),
                          start: clock, end: clock + eta });
        clock += eta;
        touch(e.agent_id).won += 1;
        v.story.push(`${e.job_id}: awarded to ${agentName(e.agent_id)} at ${money(Number(e.price))}.`);
        break;
      }
      case 'work': {
        const a = [...v.attempts].reverse().find(x => x.job === e.job_id && x.agent === e.agent_id);
        if (a) { a.tool = e.tool; a.mode = e.mode; }
        if (e.note) v.story.push(`${e.job_id}: ${e.note}.`);
        break;
      }
      case 'settled': {
        const price = Number(e.price);
        const paid = e.outcome === 'paid' ? price : 0;
        v.settlements.push({
          job: e.job_id, agent: agentName(e.agent_id), verdict: String(e.verdict), outcome: e.outcome, price,
          paid, withheld: e.outcome === 'unpaid' ? price : 0, stars_after: e.stars_after,
          reasons: (e.reasons ?? []).join('; '),
        });
        const a = [...v.attempts].reverse().find(x => x.job === e.job_id && x.agent === e.agent_id);
        if (a) a.outcome = e.outcome;
        const row = touch(e.agent_id, e.stars_after);
        row.earned += paid;
        if (e.outcome === 'unpaid') row.failed += 1;
        v.spent = Number(e.spent);
        v.story.push(e.outcome === 'paid'
          ? `${e.job_id}: review passed, ${agentName(e.agent_id)} paid ${money(price)}.`
          : e.outcome === 'unpaid'
            ? `${e.job_id}: review failed (${(e.reasons ?? [])[0] ?? 'did not meet criteria'}). ${agentName(e.agent_id)} not paid; rating ${e.stars_after}★.`
            : `${e.job_id}: held for a human; no money moved.`);
        break;
      }
      case 'blocked':
        v.blocked[e.job_id] = e.waiting_for;
        v.story.push(`${e.job_id}: blocked, waiting for ${e.waiting_for.join(', ')}.`);
        break;
      case 'unfunded':
        v.unfunded[e.job_id] = e.reason;
        v.story.push(`${e.job_id}: no affordable bid (${money(Number(e.remaining))} left).`);
        break;
      case 'error':
        v.done = true;
        v.story.push(`Run stopped: ${e.message}. No money moved.`);
        break;
      case 'done':
        v.done = true; v.spent = Number(e.spent);
        v.story.push(`Done: ${money(Number(e.spent))} of ${money(Number(e.budget))} spent; the rest goes back to the buyer.`);
        break;
    }
  }
  v.agents = [...agents.values()].sort((a, b) => b.earned - a.earned || a.agent.localeCompare(b.agent));
  return v;
}

// One row per job for the Bryntum timeline. Times are offsets in seconds.
export function timelineRows(v: MarketView) {
  return v.jobs.map(j => ({
    job: j,
    attempts: v.attempts.filter(a => a.job === j.id),
    blocked: v.blocked[j.id],
    unfunded: v.unfunded[j.id],
  }));
}
