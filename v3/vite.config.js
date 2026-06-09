import { defineConfig } from 'vite';

export default defineConfig({
  optimizeDeps: {
    exclude: ['@huggingface/transformers']
  },
  worker: {
    format: 'es'
  },
  server: {
    port: 3005,
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp'
    }
  },
  preview: {
    port: 3005,
    headers: {
      'Cross-Origin-Opener-Policy': 'same-origin',
      'Cross-Origin-Embedder-Policy': 'require-corp'
    }
  }
});
