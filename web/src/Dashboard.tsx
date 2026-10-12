// The AG Studio dashboards (bounty or wallet), in their own chunk (loaded lazily).
import { useMemo } from 'react';
import { AgStudio } from 'ag-studio-react';
import type { HuntView } from './hunt';
import type { WalletView } from './wallet';
import { guildTheme, initialState, studioData, walletData, walletInitialState, widgets } from './studio';

// View mode is just the report; Edit mode gets the data and widget editors.
const PANELS = { view: { right: [] }, edit: { left: ['data'], right: ['edit'] } };

type Props = { mode: 'view' | 'edit' } & ({ kind: 'hunt'; view: HuntView } | { kind: 'wallet'; view: WalletView });

export default function Dashboard(props: Props) {
  const data = useMemo(() => (props.kind === 'wallet' ? walletData(props.view) : studioData(props.view)),
    [props.kind, props.view]);
  return (
    <AgStudio key={props.kind} style={{ height: '100%' }} panels={PANELS as any} data={data as any}
      initialState={(props.kind === 'wallet' ? walletInitialState : initialState) as any} mode={props.mode}
      theme={guildTheme as any} widgets={widgets as any} />
  );
}
