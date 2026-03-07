import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In `npm run dev`, forward API calls to the Loom backend.
// Override with LOOM_API=http://127.0.0.1:9000 npm run dev
const backend = process.env.LOOM_API || 'http://127.0.0.1:8000'
const apiPaths = ['/project', '/projects', '/recipe', '/recipes', '/source', '/formats', '/health']

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    tailwindcss(),
    react(),
  ],
  server: {
    proxy: Object.fromEntries(apiPaths.map(p => [p, { target: backend, changeOrigin: false }])),
  },
  build: {
    chunkSizeWarningLimit: 2500,
  },
})
