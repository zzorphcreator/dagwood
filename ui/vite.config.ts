import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The project lives on /mnt/c (Windows drive under WSL), where file-change events
    // don't reliably reach Linux processes; poll so edits always reach the dev server.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/tasks': 'http://localhost:8000',
      '/runs': 'http://localhost:8000',
      '/tools': 'http://localhost:8000',
      '/models': 'http://localhost:8000',
      '/modes': 'http://localhost:8000',
    },
  },
})
