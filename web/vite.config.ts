import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/healthz': 'http://127.0.0.1:8000' } },
  build: {
    chunkSizeWarningLimit: 8000,
    rollupOptions: {
      output: {
        // Keep each sponsor library in its own cacheable chunk.
        manualChunks(id) {
          if (id.includes('node_modules/@bryntum')) return 'bryntum';
          // The wallet's statement needs only AG Grid; AG Studio (bounties) builds on it.
          if (['ag-grid-community', 'ag-grid-react', 'ag-stack', 'ag-charts-types'].some(m => id.includes(`node_modules/${m}/`))) return 'ag-grid';
          if (id.includes('node_modules/ag-')) return 'ag-studio';
          if (id.includes('node_modules/react') || id.includes('node_modules/scheduler')) return 'react';
        },
      },
    },
  },
});
