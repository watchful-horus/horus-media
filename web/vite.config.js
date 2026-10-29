import { defineConfig } from 'vite'
import { svelte } from '@sveltejs/vite-plugin-svelte'

export default defineConfig({
  plugins: [svelte()],
  // Graphs (with the chart library) is its own file, loaded when that tab opens; it is big by nature.
  build: { outDir: '../src/horus_media/static', emptyOutDir: true, chunkSizeWarningLimit: 700 },
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
})
