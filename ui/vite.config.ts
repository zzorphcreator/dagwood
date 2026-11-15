import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Where the dev server forwards API calls. compose.yaml points this at the api container.
const api = process.env.DAGWOOD_API_URL ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The project lives on /mnt/c (Windows drive under WSL), where file-change events
    // don't reliably reach Linux processes; poll so edits always reach the dev server.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/tasks': api,
      '/runs': api,
      '/tools': api,
      '/models': api,
      '/modes': api,
    },
  },
})
