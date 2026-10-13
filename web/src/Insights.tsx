// The budget card: a short summary (Claude when the server has a key), three numbers,
// what to watch, and proposed limit changes the owner applies with one tap.
import { useEffect, useState } from 'react';
import { Icon } from './icons';
import { money } from './wallet';

interface Watch { level: 'warn' | 'alert'; key: string; text: string }
interface Proposal { id: string; text: string; why: string; limits: Record<string, string> }
interface InsightsData {
  numbers: { loaded: string; spent: string; available: string; blocked_count: number; blocked_total: string;
             paid_count: number; average_payment: string };
  pace: { per_minute: string; known: boolean; lasts_minutes: number | null };
  watch: Watch[]; proposals: Proposal[]; summary: { text: string; by: string };
}

export default function Insights({ walletId, ownerKey, version, busy, apply }: {
  walletId: string; ownerKey: string; version: number; busy: boolean;
  apply: (limits: Record<string, string>) => Promise<unknown>;
}) {
  const [data, setData] = useState<InsightsData | null>(null);

  useEffect(() => {
    if (!walletId || !ownerKey) return;
    let alive = true;
    const t = window.setTimeout(() => {   // wait for a burst of events to settle
      fetch(`/api/wallets/${walletId}/insights`, { headers: { 'X-Owner-Key': ownerKey } })
        .then(r => (r.ok ? r.json() : null)).then(d => { if (alive && d) setData(d); }).catch(() => { /* keep last */ });
    }, 600);
    return () => { alive = false; window.clearTimeout(t); };
  }, [walletId, ownerKey, version]);

  if (!data) return null;
  const n = data.numbers;
  const loaded = Number(n.loaded);
  return (
    <section className="gp-card gp-insights" aria-label="Budget insights">
      <div className="gp-card-head"><h2>Your budget</h2><Icon name="rules" /></div>
      <p className="gp-summary">{data.summary.text}</p>
      <p className="gp-by-line">Written by {data.summary.by}</p>
      <dl className="gp-rules gp-insight-numbers">
        <div><dt>Spent</dt><dd>{money(Number(n.spent))}{loaded > 0 && <span className="gp-of"> of {money(loaded)}</span>}</dd></div>
        <div><dt>Stopped by Guild</dt><dd>{money(Number(n.blocked_total))}<span className="gp-of"> · {n.blocked_count} tries</span></dd></div>
        <div><dt>Pace</dt><dd>{data.pace.known ? `${money(Number(data.pace.per_minute))}/min` : <span className="gp-of">needs 10 min of history</span>}
          {data.pace.lasts_minutes !== null && <span className="gp-of"> · lasts ~{Math.round(data.pace.lasts_minutes)} min</span>}</dd></div>
      </dl>
      {data.watch.length > 0 && (
        <ul className="gp-watch" aria-label="Worth a look">
          {data.watch.map(w => (
            <li key={w.key} className={w.level}>
              <Icon name={w.level === 'alert' ? 'shieldX' : 'clock'} size={16} /> <span>{w.text}</span>
            </li>
          ))}
        </ul>
      )}
      {data.proposals.map(p => (
        <div key={p.id} className="gp-proposal">
          <div><b>{p.text}</b><span>{p.why}</span></div>
          <button className="gp-btn gp-btn-primary gp-btn-sm" disabled={busy} onClick={() => apply(p.limits)}>Apply</button>
        </div>
      ))}
    </section>
  );
}
