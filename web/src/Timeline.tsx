// Bryntum Gantt timeline of one bounty: a row per agent, a bar for its search, and a
// marker per claim at the second it arrived, green if Guild verified it, red if not.
import { useEffect, useMemo, useRef } from 'react';
import { BryntumGantt } from '@bryntum/gantt-react';
import '@bryntum/gantt/fontawesome/css/fontawesome.css';
import '@bryntum/gantt/fontawesome/css/solid.css';
import '@bryntum/gantt/gantt.css';
import '@bryntum/gantt/svalbard-light.css';
import { StringHelper } from '@bryntum/gantt';
import type { HuntView } from './hunt';

// Seconds since the bounty opened, drawn from a fixed start so identical bounties
// give identical charts.
const T0 = new Date('2026-11-02T09:00:00');
const at = (s: number) => new Date(T0.getTime() + Math.round(s * 1000));
// Static props must keep their identity: the React wrapper re-applies any prop
// that changes, and a fresh `project` object would wipe the loaded tasks.
const VIEW_PRESET = { base: 'secondAndMinute', tickWidth: 36, headers: [{ unit: 'second', increment: 1, dateFormat: 's' }] };
const COLUMNS = [{ type: 'name', text: 'Agent and claims', width: typeof window !== 'undefined' && window.innerWidth < 600 ? 150 : 300 }];
const TOOLTIP = {
  template: ({ taskRecord }: any) =>
    `<b>${StringHelper.encodeHtml(taskRecord.name)}</b><br>${StringHelper.encodeHtml(taskRecord.note ?? '')}`,
};
const END0 = at(10);

export function toProject(v: HuntView) {
  const last = Math.max(0, ...v.claims.map(c => c.at), ...v.racers.map(r => r.searched_at ?? 0));
  const tasks = v.racers.map(r => {
    const mine = v.claims.filter(c => c.agent_id === r.agent_id);
    const end = Math.max(r.searched_at ?? 0, ...mine.map(c => c.at), 0.2);
    const children = [
      ...(r.outside ? [] : [{
        id: `${r.agent_id}-search`, name: `searching${r.tool ? ` with ${r.tool}` : ''}`,
        startDate: at(0), endDate: at(Math.max(r.searched_at ?? end, 0.2)), manuallyScheduled: true,
        eventColor: r.searched_at == null ? 'blue' : 'gray', note: r.mode === 'simulated' ? 'demo data' : 'live',
      }]),
      ...mine.map(c => ({
        id: `${r.agent_id}-c${c.n}`, name: `${c.result === 'passed' ? '✓' : '✗'} $${c.claimed.toFixed(2)} · ${c.shop}`,
        startDate: at(c.at), duration: 0, manuallyScheduled: true,
        eventColor: c.result === 'passed' ? 'green' : 'red',
        note: c.reason,
      })),
    ];
    return {
      id: r.agent_id,
      name: `${r.agent}${r.outside ? ' (outside)' : ''} · ${r.passed}✓ ${r.rejected}✗`,
      expanded: true, manuallyScheduled: true, children,
      startDate: at(0), endDate: at(end),
    };
  });
  return { tasks, end: Math.max(10, Math.ceil(last) + 2) };
}

export default function Timeline({ view }: { view: HuntView }) {
  const ref = useRef<BryntumGantt>(null);
  const data = useMemo(() => toProject(view), [view]);

  // Bryntum loads are async; overlapping loads can leave the store empty. Load one
  // at a time and always finish on the newest data.
  const latest = useRef(data);
  const loading = useRef(false);
  latest.current = data;

  useEffect(() => {
    if (loading.current) return;
    loading.current = true;
    (async () => {
      let loaded: typeof data | null = null;
      let into: unknown = null;
      try {
        // Re-read the instance each pass: React may replace it (StrictMode remount).
        for (let g = ref.current?.instance; g && !g.isDestroyed && (loaded !== latest.current || into !== g);
             g = ref.current?.instance) {
          loaded = latest.current; into = g;
          // Bryntum takes ownership of the objects it loads, so hand it fresh copies.
          await g.project.loadInlineData(structuredClone({ tasks: loaded.tasks }));
          await g.project.commitAsync();
          if (!g.isDestroyed) g.setTimeSpan(T0, at(loaded.end));
        }
      } catch (err) {
        console.error('timeline load failed', err);
      } finally {
        loading.current = false;
      }
    })();
  }, [data]);

  return (
    <BryntumGantt
      ref={ref}
      startDate={T0}
      endDate={END0}
      viewPreset={VIEW_PRESET as any}
      rowHeight={40}
      barMargin={8}
      readOnly
      columns={COLUMNS as any}
      taskTooltipFeature={TOOLTIP as any}
      taskMenuFeature={false}
      projectLinesFeature={false}
      cellMenuFeature={false}
      headerMenuFeature={false}
      taskEditFeature={false}
    />
  );
}
