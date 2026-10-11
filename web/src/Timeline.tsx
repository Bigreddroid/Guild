// Bryntum Gantt timeline: one row per job, one bar per attempt, coloured by the
// reviewer's outcome. Dependencies show that the team email waits on everything else.
import { useEffect, useMemo, useRef } from 'react';
import { BryntumGantt } from '@bryntum/gantt-react';
import '@bryntum/gantt/fontawesome/css/fontawesome.css';
import '@bryntum/gantt/fontawesome/css/solid.css';
import '@bryntum/gantt/gantt.css';
import '@bryntum/gantt/svalbard-light.css';
import { StringHelper } from '@bryntum/gantt';
import type { MarketView } from './market';
import { agentName, timelineRows } from './market';

// The run's clock is agent work time in seconds, drawn from a fixed start so the
// chart is identical for identical runs.
const T0 = new Date('2026-11-02T09:00:00');
const at = (s: number) => new Date(T0.getTime() + s * 1000);
// Static props must keep their identity: the React wrapper re-applies any prop
// that changes, and a fresh `project` object would wipe the loaded tasks.
const VIEW_PRESET = { base: 'minuteAndHour', tickWidth: 40, headers: [{ unit: 'minute', increment: 1, dateFormat: 'mm' }] };
const COLUMNS = [{ type: 'name', text: 'Job and attempts', width: typeof window !== 'undefined' && window.innerWidth < 600 ? 150 : 320 }];
const TOOLTIP = {
  template: ({ taskRecord }: any) =>
    `<b>${StringHelper.encodeHtml(taskRecord.name)}</b><br>${StringHelper.encodeHtml(taskRecord.note ?? '')}`,
};
const END0 = at(600);
const COLORS: Record<string, string> = { paid: 'green', unpaid: 'red', held: 'orange' };

function toProject(v: MarketView) {
  const tasks = timelineRows(v).map(({ job, attempts, blocked, unfunded }) => {
    const status = unfunded ? 'no affordable bid' : blocked ? `blocked by ${blocked.join(', ')}` : '';
    const children = attempts.map((a, i) => ({
      id: `${job.id}-${i + 1}`,
      name: `${agentName(a.agent)} · $${a.price.toFixed(2)}${a.outcome ? ` · ${a.outcome}` : ' · working'}`,
      startDate: at(a.start), endDate: at(a.end), manuallyScheduled: true,
      eventColor: a.outcome ? COLORS[a.outcome] ?? 'gray' : 'blue',
      note: [a.tool && `tool: ${a.tool}`, a.mode && `(${a.mode})`].filter(Boolean).join(' '),
    }));
    return {
      id: job.id,
      name: status ? `${job.title} (${status})` : job.title,
      expanded: true,
      manuallyScheduled: true,
      ...(attempts.length
        ? { children, startDate: at(attempts[0].start), endDate: at(attempts[attempts.length - 1].end) }
        : { unscheduled: true }),
    };
  });
  const ids = new Set(v.jobs.map(j => j.id));
  const dependencies = v.jobs.flatMap(j =>
    j.depends_on.filter(d => ids.has(d)).map(d => ({ id: `${d}->${j.id}`, from: d, to: j.id })));
  const end = Math.max(600, ...v.attempts.map(a => a.end + 60));
  return { tasks, dependencies, end };
}

export default function Timeline({ view }: { view: MarketView }) {
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
          await g.project.loadInlineData(structuredClone({ tasks: loaded.tasks, dependencies: loaded.dependencies }));
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
