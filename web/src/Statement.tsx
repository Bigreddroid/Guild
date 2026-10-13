// The wallet's statement: every line of money in, money out and what was stopped,
// in an AG Grid (Community) table with search, CSV export and what is left in the wallet.
// Loaded lazily, in its own chunk.
import { useMemo, useRef, useState } from 'react';
import { AgGridReact, type CustomCellRendererProps } from 'ag-grid-react';
import {
  CellStyleModule, ClientSideRowModelModule, CsvExportModule, ModuleRegistry, NumberFilterModule,
  QuickFilterModule, TextFilterModule, themeQuartz, type ColDef,
} from 'ag-grid-community';
import { Icon } from './icons';
import { money, type Activity } from './wallet';

ModuleRegistry.registerModules([ClientSideRowModelModule, QuickFilterModule, CsvExportModule, TextFilterModule,
                                NumberFilterModule, CellStyleModule]);

const theme = themeQuartz.withParams({
  fontFamily: 'Inter, system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
  fontSize: 14, headerFontSize: 13, headerFontWeight: 600, spacing: 6,
  foregroundColor: '#0f172a', backgroundColor: '#ffffff', accentColor: '#1d4ed8',
  headerBackgroundColor: '#f8fafc', headerTextColor: '#475569', borderColor: '#e2e8f0',
  wrapperBorderRadius: 12, rowHoverColor: '#f8fafc', oddRowBackgroundColor: '#ffffff',
});

const STATUS: Record<string, string> = {
  paid: 'Paid', approved: 'Paying', blocked: 'Blocked', pending: 'Needs your OK', held: 'On hold', rejected: 'Declined',
  failed: 'Failed', voided: 'Released', refunded: 'Refunded', added: 'Added', returned: 'Returned',
};

export interface StatementRow {
  n: number; description: string; merchant: string; category: string; status: string; amount: number;
  moved: number; ref: string;
}

// Money actually moved: + into the wallet, − out of it; nothing for blocked, waiting or released lines.
export function statementRows(activity: Activity[]): StatementRow[] {
  return [...activity].reverse().map((a, i) => {
    const amount = a.amount ?? 0;
    const moved = a.status === 'added' ? amount : a.status === 'paid' || a.status === 'returned' ? -amount : 0;
    return { n: i + 1, description: a.title, merchant: a.purchase ? a.purchase.shop : 'PayPal',
             category: a.purchase ? a.purchase.category : 'wallet', status: a.status, amount, moved, ref: a.ref ?? '' };
  });
}

function StatusCell(p: CustomCellRendererProps<StatementRow>) {
  const s = String(p.value ?? '');
  return <span className={`gp-pill pill-${s}`}>{STATUS[s] ?? s}</span>;
}

const usd = (v: unknown) => (typeof v === 'number' ? money(v) : '');
const signed = (v: unknown) => (typeof v !== 'number' ? '' : v > 0 ? `+${money(v)}` : v < 0 ? `−${money(-v)}` : '—');

const cap = (v: unknown) => { const t = String(v ?? ''); return t.charAt(0).toUpperCase() + t.slice(1); };
const isStopped = (r?: StatementRow) => !!r && r.moved === 0 && !['pending', 'held', 'approved'].includes(r.status);

// Most important first, so a narrow screen shows what happened and what it cost before scrolling.
// Typed once at module level (contextual typing keeps each column a ColDef).
// The minimum widths add up to 1024px: .gp-statement .ag-root-wrapper keeps the grid at least that wide,
// so the grid never needs its own horizontal scrollbar (which auto-height doesn't leave room for).
const COLUMNS: ColDef<StatementRow>[] = [
  { field: 'n', headerName: '#', width: 64, sort: 'desc', filter: false },
  { field: 'description', headerName: 'Description', flex: 2, minWidth: 180, filter: 'agTextColumnFilter' },
  { field: 'status', headerName: 'Status', width: 130, cellRenderer: StatusCell, filter: 'agTextColumnFilter',
    valueFormatter: p => STATUS[p.value] ?? p.value, getQuickFilterText: p => STATUS[p.value] ?? p.value },
  { field: 'moved', headerName: 'Money moved', width: 130, type: 'rightAligned', filter: 'agNumberColumnFilter',
    valueFormatter: p => signed(p.value),
    cellClassRules: { 'gp-cell-in': p => (p.value ?? 0) > 0, 'gp-cell-out': p => (p.value ?? 0) < 0 } },
  { field: 'amount', headerName: 'Amount', width: 110, type: 'rightAligned', filter: 'agNumberColumnFilter',
    valueFormatter: p => usd(p.value), cellClassRules: { 'gp-cell-struck': p => isStopped(p.data) } },
  { field: 'merchant', headerName: 'Paid to / from', flex: 1, minWidth: 150, filter: 'agTextColumnFilter' },
  { field: 'category', headerName: 'Category', width: 110, filter: 'agTextColumnFilter', valueFormatter: p => cap(p.value) },
  { field: 'ref', headerName: 'PayPal reference', flex: 1, minWidth: 150, cellClass: 'gp-mono',
    filter: 'agTextColumnFilter' },
];

export default function Statement({ activity }: { activity: Activity[] }) {
  const grid = useRef<AgGridReact<StatementRow>>(null);
  const [search, setSearch] = useState('');
  const rows = useMemo(() => statementRows(activity), [activity]);
  const left = useMemo(() => rows.reduce((s, r) => s + r.moved, 0), [rows]);

  const exportCsv = () => grid.current?.api.exportDataAsCsv({
    fileName: 'guild-pay-statement.csv',
    processCellCallback: p => (p.column.getColId() === 'status' ? STATUS[p.value] ?? p.value : p.value),
  });

  return (
    <section className="gp-card" aria-label="Statement">
      <div className="gp-card-head">
        <h2>Statement <span className="gp-by">AG Grid</span></h2>
        <div className="gp-statement-tools">
          <label className="gp-search">
            <span className="sr">Search the statement</span>
            <input type="search" placeholder="Search" value={search} onChange={e => setSearch(e.target.value)} />
          </label>
          <button className="gp-btn gp-btn-quiet gp-btn-sm" onClick={exportCsv} disabled={!rows.length}>
            <Icon name="out" size={16} /> Export CSV
          </button>
        </div>
      </div>
      <div className="gp-statement">
        <AgGridReact<StatementRow>
          ref={grid}
          theme={theme}
          rowData={rows}
          columnDefs={COLUMNS}
          defaultColDef={{ sortable: true, resizable: false, suppressMovable: true }}
          quickFilterText={search}
          domLayout="autoHeight"
          getRowId={p => String(p.data.n)}
          overlayNoRowsTemplate="<span class='gp-empty'>No money has moved yet.</span>"
          suppressCellFocus
        />
      </div>
      <div className="gp-statement-foot">
        <span>Left in wallet (money in − money out)</span>
        <b className={left > 0 ? 'gp-cell-in' : ''}>{signed(left)}</b>
      </div>
      <p className="gp-small">Blocked, declined and released lines are struck through: no money moved for them.</p>
    </section>
  );
}
