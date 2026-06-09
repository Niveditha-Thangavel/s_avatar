const CACHE_NAME = 'avatar-app-cache-v1';
const MODEL_CACHE_NAME = 'kokoro-model-cache';

// Assets to cache on install
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/index.css',
  '/main.js',
  '/avatar_portrait.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return Promise.allSettled(
        ASSETS_TO_CACHE.map(url => cache.add(url))
      ).then(() => self.skipWaiting());
    })
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((key) => {
          if (key !== CACHE_NAME && key !== MODEL_CACHE_NAME) {
            return caches.delete(key);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Check if it's a model file request to Hugging Face
  const isModelRequest = url.hostname.includes('huggingface.co') || 
                         url.pathname.includes('.onnx') || 
                         url.pathname.includes('.json');

  if (isModelRequest) {
    event.respondWith(
      caches.open(MODEL_CACHE_NAME).then((cache) => {
        return cache.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            return cachedResponse;
          }
          return fetch(event.request).then((networkResponse) => {
            if (networkResponse.status === 200) {
              cache.put(event.request, networkResponse.clone());
            }
            return networkResponse;
          }).catch(err => {
            console.error('Service worker failed to fetch model file:', err);
            throw err;
          });
        });
      })
    );
  } else {
    // Bypass caching for local assets in development to ensure code changes are instant
    if (url.origin === self.location.origin) {
      event.respondWith(fetch(event.request));
    } else {
      // Stale-while-revalidate for local assets
      event.respondWith(
        caches.match(event.request).then((cachedResponse) => {
          if (cachedResponse) {
            fetch(event.request).then((networkResponse) => {
              if (networkResponse.status === 200) {
                caches.open(CACHE_NAME).then((cache) => {
                  cache.put(event.request, networkResponse);
                });
              }
            }).catch(() => {});
            return cachedResponse;
          }
          return fetch(event.request);
        })
      );
    }
  }
});
