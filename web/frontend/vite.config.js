import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

export default defineConfig({
  plugins: [svelte()],
  server: {
    // `npm run dev` talks to a running bimmerdash-web on :8320
    proxy: { '/api': 'http://localhost:8320' },
  },
  build: { outDir: 'dist', emptyOutDir: true },
});
