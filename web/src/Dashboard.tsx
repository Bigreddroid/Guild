// The AG Studio bounty dashboard, in its own chunk (loaded lazily).
import { useMemo } from 'react';
import { AgStudio } from 'ag-studio-react';
import type { HuntView } from './hunt';
import { guildTheme, initialState, studioData, widgets } from './studio';

// View mode is just the report; Edit mode gets the data and widget editors.
const PANELS = { view: { right: [] }, edit: { left: ['data'], right: ['edit'] } };

type Props = { mode: 'view' | 'edit'; kind: 'hunt'; view: HuntView };

export default function Dashboard(props: Props) {
  const data = useMemo(() => studioData(props.view), [props.view]);
  return (
    <AgStudio style={{ height: '100%' }} panels={PANELS as any} data={data as any}
      initialState={initialState as any} mode={props.mode} theme={guildTheme as any} widgets={widgets as any} />
  );
}
