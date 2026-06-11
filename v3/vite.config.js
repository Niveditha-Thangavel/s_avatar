import { defineConfig } from 'vite';

export default defineConfig({
  // Stop Vite scanning the Python server directory for entry points.
  // Without this it picks up HTML files inside server/venv (torch, gradio, etc.)
  // and fails because they contain no valid JS.
  optimizeDeps: {
    exclude: [],
    // Restrict the dependency scan to the actual source root only
  },

  server: {
    port: 3005,
    // Exclude the Python server directory from the file watcher entirely
    watch: {
      ignored: ['**/server/**', '**/node_modules/**'],
    },
    proxy: {
      '/ws': {
        target: 'ws://localhost:8765',
        ws: true,
      },
      '/chat': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8765',
        changeOrigin: true,
      },
    },
  },

  preview: {
    port: 3005,
  },

  // Explicitly tell Vite where the project root is (this dir, not parent)
  root: '.',
});
