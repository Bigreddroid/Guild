// AI tools the wallet pays for every period: what renews, when, whether it's used, and one-tap cancel.
import { Icon } from './icons';
import { money, type Renewal } from './wallet';

const STATUS: Record<string, string> = { active: 'Active', waiting: 'Needs your OK', paused: 'Paused', cancelled: 'Cancelled' };

function every(minutes: number): string {
  if (minutes >= 60 * 24 * 28) return 'month';
  if (minutes >= 60 * 24) return `${Math.round(minutes / 1440)} days`;
  if (minutes >= 60) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes)} min`;
}

function when(seconds: number | null): string {
  if (seconds === null) return '';
  if (seconds < 90) return `${seconds}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${Math.round(seconds / 3600)} h`;
}

export default function Renewals({ renewals, owner, busy, closed, canStart, start, act }: {
  renewals: Renewal[]; owner: boolean; busy: boolean; closed: boolean; canStart: boolean;
  start: () => void; act: (id: string, action: 'used' | 'cancel' | 'resume') => void;
}) {
  return (
    <section className="gp-card" aria-label="AI tools and renewals">
      <div className="gp-card-head"><h2>AI tools it pays for</h2><Icon name="clock" /></div>
      <p className="gp-small">Subscriptions your AI keeps. Each renewal is checked again like a new purchase (real seller,
        same price) and paid from the wallet. A tool nobody uses for two periods is flagged.</p>
      {renewals.length === 0 && owner && !closed && (
        <button className="gp-btn gp-btn-outline" disabled={busy || !canStart} onClick={start}>
          Subscribe to the demo AI tool · $20 every 2 min
        </button>
      )}
      <ul className="gp-renewals">
        {renewals.map(r => (
          <li key={r.id} className={`gp-renewal st-${r.status}`}>
            <div className="gp-renewal-head">
              <b>{r.tool}</b>
              <span className={`gp-pill pill-${r.status === 'active' ? 'paid' : r.status === 'waiting' ? 'pending' : r.status === 'paused' ? 'blocked' : 'rejected'}`}>
                {STATUS[r.status] ?? r.status}
              </span>
            </div>
            <div className="gp-renewal-meta">
              {money(r.price)} every {every(r.every_minutes)} · paid {r.charges}×
              {r.next_in_s !== null && <> · next in {when(r.next_in_s)}</>}
              {r.booked_by === 'agent' && <> · started by your AI</>}
            </div>
            {r.unused && <div className="gp-unused"><Icon name="clock" size={14} /> Not used for two periods. Cancel it?</div>}
            {r.reason && r.status !== 'active' && <div className="gp-renewal-why">{r.reason}</div>}
            {owner && !closed && r.status !== 'cancelled' && (
              <div className="gp-renewal-btns">
                <button className="gp-btn gp-btn-quiet gp-btn-sm" disabled={busy} onClick={() => act(r.id, 'used')}>Report use</button>
                {r.status === 'paused' && <button className="gp-btn gp-btn-quiet gp-btn-sm" disabled={busy} onClick={() => act(r.id, 'resume')}>Try again</button>}
                <button className="gp-btn gp-btn-quiet gp-btn-sm" disabled={busy} onClick={() => act(r.id, 'cancel')}>Cancel</button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
