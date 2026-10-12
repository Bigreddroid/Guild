// AG Studio set-up for the bounty dashboard: data sources, the starting report
// (pages and widgets), the Guild theme, and star-rating and result cell renderers.
import type { CustomCellRendererProps } from 'ag-grid-react';
import { createWidgets } from 'ag-studio-react';
import { studioTheme } from 'ag-studio';
import type { HuntView } from './hunt';
import type { WalletView } from './wallet';

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const f = (id: string, format: string, name?: string) => ({
  id, format, ...(name ? { name } : {}), ...(format === 'currencyFormat' ? { formatOptions: { format: USD } } : {}),
});

export function studioData(v: HuntView) {
  const best = v.winner?.price ?? 0;
  return {
    description: 'Live data from one Guild deal bounty: every claim agents made and what Guild found on the page.',
    sources: [
      {
        id: 'claims', name: 'Claims', description: 'Every claimed deal, the price on the page, and why it passed or failed.',
        data: v.claims.map(c => ({ ...c, page_price: c.page_price ?? undefined, origin: c.outside ? 'outside' : 'Guild' })),
        fields: [f('n', 'integerFormat', '#'), f('agent', 'textFormat', 'Agent'), f('origin', 'textFormat', 'Built by'),
                 f('shop', 'textFormat', 'Shop'), f('claimed', 'currencyFormat', 'Claimed'),
                 f('page_price', 'currencyFormat', 'Page says'), f('result', 'textFormat', 'Result'),
                 f('reason', 'textFormat', 'Why'), f('at', 'decimalFormat', 'At (s)'),
                 f('stars_after', 'decimalFormat', 'Rating after'), f('passed', 'integerFormat', 'Passed'),
                 f('rejected', 'integerFormat', 'Rejected'), f('url', 'textFormat', 'Link')],
      },
      {
        id: 'agents', name: 'Agents', description: 'Each agent in this bounty: rating before and after, claims allowed and made.',
        data: v.racers.map(r => ({ ...r, origin: r.outside ? 'outside' : 'Guild' })),
        fields: [f('agent', 'textFormat', 'Agent'), f('origin', 'textFormat', 'Built by'), f('tool', 'textFormat', 'Tool'),
                 f('mode', 'textFormat', 'Mode'), f('stars_before', 'decimalFormat', 'Rating before'),
                 f('stars', 'decimalFormat', 'Rating now'), f('max_claims', 'integerFormat', 'Claims allowed'),
                 f('claims', 'integerFormat', 'Claims'), f('passed', 'integerFormat', 'Passed'),
                 f('rejected', 'integerFormat', 'Rejected')],
      },
      {
        // Always passed then rejected, so the chart palette gives them green then red.
        id: 'results', name: 'Results', description: 'How many claims passed and how many were rejected.',
        data: (['passed', 'rejected'] as const).map(result => ({ result, claims: v.claims.filter(c => c.result === result).length })),
        fields: [f('result', 'textFormat', 'Result'), f('claims', 'integerFormat', 'Claims')],
      },
      {
        id: 'deal', name: 'Deal', description: 'The best verified price, what the buyer saved, and the bounty.',
        data: [{ best, saved: v.winner?.saved ?? 0, max_price: v.want?.max_price ?? 0, bounty: v.want?.bounty ?? 0,
                 paid: v.winner ? v.want?.bounty ?? 0 : 0, mode: v.payment ? String(v.payment.mode) : 'pending' }],
        fields: [f('best', 'currencyFormat', 'Best verified price'), f('saved', 'currencyFormat', 'Under max by'),
                 f('max_price', 'currencyFormat', 'Max price'), f('bounty', 'currencyFormat', 'Bounty'),
                 f('paid', 'currencyFormat', 'Bounty paid'), f('mode', 'textFormat', 'PayPal mode')],
      },
    ],
  };
}

const ref = (id: string, aggregation?: string) => ({ id, ...(aggregation ? { aggregation } : {}) });
const title = (text: string) => ({ title: { enabled: true, text } });
const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number) => ({ xTrack, yTrack, xSpan, ySpan });

export const initialState = {
  selectedPageId: 'bounty',
  pages: [{
    id: 'bounty',
    widgets: {
      best: { type: 'value', dataMapping: { value: [ref('deal.best', 'sum')] }, format: title('Best verified price') },
      saved: { type: 'value', dataMapping: { value: [ref('deal.saved', 'sum')] }, format: title('Under your max') },
      passed: { type: 'value', dataMapping: { value: [ref('claims.passed', 'sum')] }, format: title('Claims passed') },
      rejected: { type: 'value', dataMapping: { value: [ref('claims.rejected', 'sum')] }, format: title('Claims rejected') },
      byAgent: {
        type: 'bar-chart-grouped',
        dataMapping: { categoryKey: [ref('claims.agent')], valueKey: [ref('claims.passed', 'sum'), ref('claims.rejected', 'sum')] },
        format: title('Claims by agent: passed vs rejected'),
      },
      outcome: {
        type: 'donut-chart',
        dataMapping: { categoryKey: [ref('results.result')], valueKey: [ref('results.claims', 'sum')] },
        format: title('Claim results'),
      },
      claims: {
        type: 'grid',
        dataMapping: { cols: ['n', 'agent', 'shop', 'claimed', 'page_price', 'result', 'reason', 'at'].map(c => ref(`claims.${c}`)) },
        format: title('Every claim, checked against the shop\'s own page'),
      },
      agents: {
        type: 'grid',
        dataMapping: { cols: ['agent', 'origin', 'tool', 'stars_before', 'stars', 'max_claims', 'passed', 'rejected'].map(c => ref(`agents.${c}`)) },
        format: title('Agents in this bounty'),
      },
    },
    widgetLayout: {
      best: at(0, 0, 6, 7), saved: at(6, 0, 6, 7), passed: at(12, 0, 6, 7), rejected: at(18, 0, 6, 7),
      byAgent: at(0, 7, 14, 17), outcome: at(14, 7, 10, 17),
      claims: at(0, 24, 24, 20), agents: at(0, 44, 24, 14),
    },
  }],
};

// ------------------------------------------------------------------ Guild Pay wallet

export function walletData(v: WalletView) {
  const by = (s: string) => v.purchases.filter(p => p.status === s).length;
  return {
    description: 'One Guild Pay wallet: every purchase the agent asked for and what the wallet decided.',
    sources: [
      {
        id: 'purchases', name: 'Purchases', description: 'Each purchase request, the decision, and why.',
        data: v.purchases.map((p, i) => ({ n: i + 1, item: p.item, shop: p.shop, price: p.price, status: p.status,
                                            why: p.reasons.join('; '), at: p.at, seller: p.seller || p.shop,
                                            paid: p.status === 'paid' ? p.price : 0,
                                            stopped: p.status === 'blocked' ? p.price : 0 })),
        fields: [f('n', 'integerFormat', '#'), f('item', 'textFormat', 'Item'), f('shop', 'textFormat', 'Shop'),
                 f('price', 'currencyFormat', 'Price'), f('status', 'textFormat', 'Decision'), f('why', 'textFormat', 'Why'),
                 f('at', 'decimalFormat', 'At (s)'), f('seller', 'textFormat', 'Paid to'),
                 f('paid', 'currencyFormat', 'Paid'), f('stopped', 'currencyFormat', 'Blocked')],
      },
      {
        // Fixed order so the palette gives approved green, blocked red, waiting amber.
        id: 'decisions', name: 'Decisions', description: 'How many requests each decision got.',
        data: [{ decision: 'paid or approved', count: by('paid') + by('approved') }, { decision: 'blocked', count: by('blocked') },
               { decision: 'waiting for you', count: by('pending') }, { decision: 'rejected or refunded', count: by('rejected') + by('refunded') }],
        fields: [f('decision', 'textFormat', 'Decision'), f('count', 'integerFormat', 'Requests')],
      },
      {
        id: 'budget', name: 'Budget', description: 'The owner budget and where it stands.',
        data: [{ ...v.totals, blocked: v.purchases.filter(p => p.status === 'blocked').reduce((s, p) => s + p.price, 0) }],
        fields: [f('budget', 'currencyFormat', 'Budget'), f('spent', 'currencyFormat', 'Spent'),
                 f('reserved', 'currencyFormat', 'Waiting for you'), f('remaining', 'currencyFormat', 'Left'),
                 f('blocked', 'currencyFormat', 'Blocked spend')],
      },
    ],
  };
}

export const walletInitialState = {
  selectedPageId: 'wallet',
  pages: [{
    id: 'wallet',
    widgets: {
      spent: { type: 'value', dataMapping: { value: [ref('budget.spent', 'sum')] }, format: title('Spent') },
      left: { type: 'value', dataMapping: { value: [ref('budget.remaining', 'sum')] }, format: title('Left') },
      waiting: { type: 'value', dataMapping: { value: [ref('budget.reserved', 'sum')] }, format: title('Waiting for you') },
      blocked: { type: 'value', dataMapping: { value: [ref('budget.blocked', 'sum')] }, format: title('Bad spend blocked') },
      decisions: {
        type: 'donut-chart',
        dataMapping: { categoryKey: [ref('decisions.decision')], valueKey: [ref('decisions.count', 'sum')] },
        format: title('What the wallet decided'),
      },
      byShop: {
        type: 'bar-chart-grouped',
        dataMapping: { categoryKey: [ref('purchases.shop')], valueKey: [ref('purchases.paid', 'sum'), ref('purchases.stopped', 'sum')] },
        format: title('By shop: paid vs blocked'),
      },
      purchases: {
        type: 'grid',
        dataMapping: { cols: ['n', 'item', 'shop', 'price', 'status', 'why'].map(c => ref(`purchases.${c}`)) },
        format: title('Every purchase request, checked before any money moved'),
      },
    },
    widgetLayout: {
      spent: at(0, 0, 6, 7), left: at(6, 0, 6, 7), waiting: at(12, 0, 6, 7), blocked: at(18, 0, 6, 7),
      decisions: at(0, 7, 10, 17), byShop: at(10, 7, 14, 17), purchases: at(0, 24, 24, 20),
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
  // Series 1 is always "passed" and series 2 "rejected".
  chartPaletteFills1Color: '#2e9e5b', chartPaletteStrokes1Color: '#1f7a43',
  chartPaletteFills2Color: '#d9534f', chartPaletteStrokes2Color: '#b42318',
  chartPaletteFills3Color: '#e3a008', chartPaletteStrokes3Color: '#b45309',
  chartPaletteFills4Color: '#98a2b3', chartPaletteStrokes4Color: '#667085',
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
        if (/\.(stars|stars_after|stars_before)$/.test(id)) return StarsCell;
        if (/\.(result|origin|status)$/.test(id)) return StatusCell;
        return undefined;
      },
    },
  }],
} as any);
