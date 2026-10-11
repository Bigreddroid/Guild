// The AG Studio market dashboard, in its own chunk (loaded lazily by App).
import { useMemo } from 'react';
import { AgStudio } from 'ag-studio-react';
import type { MarketView } from './market';
import { guildTheme, initialState, studioData, widgets } from './studio';

export default function Dashboard({ view, mode }: { view: MarketView; mode: 'view' | 'edit' }) {
  const data = useMemo(() => studioData(view), [view]);
  return (
    <AgStudio style={{ height: '100%' }} panels={{ view: { right: [] } } as any} data={data as any}
      initialState={initialState as any} mode={mode} theme={guildTheme as any} widgets={widgets as any} />
  );
}
