// AG Studio set-up for the market dashboard: data sources, the starting report
// (pages and widgets), the Guild theme, and a star-rating cell renderer.
import type { CustomCellRendererProps } from 'ag-grid-react';
import { createWidgets } from 'ag-studio-react';
import { studioTheme } from 'ag-studio';
import type { MarketView } from './market';

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const f = (id: string, format: string, name?: string) => ({
  id, format, ...(name ? { name } : {}), ...(format === 'currencyFormat' ? { formatOptions: { format: USD } } : {}),
});

export function studioData(v: MarketView) {
  return {
    description: 'Live data from one run of the Guild agent job market.',
    sources: [
      {
        id: 'bids', name: 'Bids', description: 'Every bid on every job, with how the market ranked it.',
        data: v.bids,
        fields: [f('job', 'textFormat', 'Job'), f('attempt', 'integerFormat', 'Round'), f('agent', 'textFormat', 'Agent'),
                 f('price', 'currencyFormat', 'Price'), f('eta_s', 'integerFormat', 'ETA (s)'),
                 f('stars', 'decimalFormat', 'Rating'), f('value', 'decimalFormat', 'Value score'),
                 f('status', 'textFormat', 'Result'), f('reason', 'textFormat', 'Why')],
      },
      {
        id: 'settlements', name: 'Settlements', description: 'Reviewer verdict and payment for each piece of work.',
        data: v.settlements.map(s => ({ ...s, n: 1 })),
        fields: [f('job', 'textFormat', 'Job'), f('agent', 'textFormat', 'Agent'), f('verdict', 'textFormat', 'Verdict'),
                 f('outcome', 'textFormat', 'Outcome'), f('price', 'currencyFormat', 'Bid'),
                 f('paid', 'currencyFormat', 'Paid'), f('withheld', 'currencyFormat', 'Withheld'),
                 f('stars_after', 'decimalFormat', 'Rating after'), f('reasons', 'textFormat', 'Reviewer notes'),
                 f('n', 'integerFormat', 'Count')],
      },
      {
        id: 'agents', name: 'Agents', description: 'Each agent: rating, jobs won and failed, and earnings.',
        data: v.agents,
        fields: [f('agent', 'textFormat', 'Agent'), f('stars', 'decimalFormat', 'Rating'), f('earned', 'currencyFormat', 'Earned'),
                 f('won', 'integerFormat', 'Won'), f('failed', 'integerFormat', 'Failed')],
      },
      {
        id: 'budget', name: 'Budget', description: 'The buyer budget held on PayPal and how much was spent.',
        data: [{ budget: v.budget, spent: v.spent, remaining: +(v.budget - v.spent).toFixed(2),
                 mode: v.payment ? String(v.payment.mode) : 'pending' }],
        fields: [f('budget', 'currencyFormat', 'Budget'), f('spent', 'currencyFormat', 'Spent'),
                 f('remaining', 'currencyFormat', 'Released to buyer'), f('mode', 'textFormat', 'PayPal mode')],
      },
    ],
  };
}

const ref = (id: string, aggregation?: string) => ({ id, ...(aggregation ? { aggregation } : {}) });
const title = (text: string) => ({ title: { enabled: true, text } });
const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number) => ({ xTrack, yTrack, xSpan, ySpan });

export const initialState = {
  selectedPageId: 'market',
  pages: [{
    id: 'market',
    widgets: {
      spent: { type: 'value', dataMapping: { value: [ref('budget.spent', 'sum')] }, format: title('Paid out') },
      withheld: { type: 'value', dataMapping: { value: [ref('settlements.withheld', 'sum')] }, format: title('Withheld') },
      released: { type: 'value', dataMapping: { value: [ref('budget.remaining', 'sum')] }, format: title('Released') },
      jobs: { type: 'value', dataMapping: { value: [ref('settlements.n', 'sum')] }, format: title('Reviews') },
      earnings: {
        type: 'bar-chart-grouped',
        dataMapping: { categoryKey: [ref('settlements.agent')], valueKey: [ref('settlements.paid', 'sum')] },
        format: title('Earnings by agent'),
      },
      outcomes: {
        type: 'donut-chart',
        dataMapping: { categoryKey: [ref('settlements.outcome')], valueKey: [ref('settlements.n', 'sum')] },
        format: title('Review outcomes'),
      },
      bids: {
        type: 'grid',
        dataMapping: { cols: ['job', 'attempt', 'agent', 'price', 'stars', 'value', 'status', 'reason'].map(c => ref(`bids.${c}`)) },
        format: title('Every bid, ranked by value = rating² ÷ price'),
      },
      reputation: {
        type: 'grid',
        dataMapping: { cols: ['agent', 'stars', 'won', 'failed', 'earned'].map(c => ref(`agents.${c}`)) },
        format: title('Agent reputation'),
      },
    },
    widgetLayout: {
      spent: at(0, 0, 6, 7), withheld: at(6, 0, 6, 7), released: at(12, 0, 6, 7), jobs: at(18, 0, 6, 7),
      earnings: at(0, 7, 12, 17), outcomes: at(12, 7, 12, 17),
      bids: at(0, 24, 24, 20), reputation: at(0, 44, 24, 14),
    },
  }],
};

export const guildTheme = studioTheme.withParams({
  accentColor: '#1f5fbf',
  fontFamily: 'Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
  textColor: '#14213d',
  backgroundColor: '#ffffff',
  studioMinWidth: 320,
  studioCanvasMinWidth: 320,
} as any);

// Rating cells show stars, so a judge sees reputation at a glance.
function StarsCell(p: CustomCellRendererProps) {
  const n = typeof p.value === 'number' ? p.value : Number(p.value);
  if (!Number.isFinite(n)) return <span>{p.valueFormatted ?? ''}</span>;
  const full = Math.round(n);
  return (
    <span className="stars" title={`${n.toFixed(1)} of 5`} aria-label={`${n.toFixed(1)} of 5 stars`}>
      <span className="on">{'★'.repeat(full)}</span><span className="off">{'★'.repeat(Math.max(0, 5 - full))}</span>
      <span className="num">{n.toFixed(1)}</span>
    </span>
  );
}

function StatusCell(p: CustomCellRendererProps) {
  const s = String(p.value ?? '');
  return <span className={`pill pill-${s}`}>{s}</span>;
}

export const widgets = createWidgets({
  overrides: [{
    id: 'grid',
    options: {
      createCellRenderer: (field: any) => {
        const id = String(field?.id ?? field?.fieldId ?? '');
        if (/\.(stars|stars_after)$/.test(id)) return StarsCell;
        if (/\.(status|outcome)$/.test(id)) return StatusCell;
        return undefined;
      },
    },
  }],
} as any);
