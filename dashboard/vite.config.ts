import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  base: '/',
  server: { proxy: { '/api': 'http://127.0.0.1:8080', '/healthz': 'http://127.0.0.1:8080' } },
  build: {
    outDir: '../packages/mas-server/src/mas_server/dashboard_assets',
    emptyOutDir: true,
    sourcemap: false,
    rolldownOptions: {
      output: {
        entryFileNames: 'assets/dashboard.js',
        assetFileNames: 'assets/dashboard.[ext]',
        codeSplitting: false,
      },
    },
  },
});
