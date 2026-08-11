const CACHE_NAME = 'north-pos-v1';
const ASSETS_TO_CACHE = [
    '/pos/',
    '/static/pos/pos_db.js',
    'https://cdn.tailwindcss.com',
    'https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js',
    'https://unpkg.com/htmx.org@1.9.10'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            console.log('[SW] Pre-caching static assets for POS');
            return cache.addAll(ASSETS_TO_CACHE).catch(err => {
                console.warn('[SW] Caching failed for some assets, continuing:', err);
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    // Para endpoints de API, intentar red primero, fallback a respuesta vacía o de cache si aplica
    if (event.request.url.includes('/pos/api/')) {
        event.respondWith(
            fetch(event.request).catch(() => {
                return new Response(JSON.stringify({ status: 'offline', offline: true }), {
                    headers: { 'Content-Type': 'application/json' }
                });
            })
        );
        return;
    }

    // Para páginas estáticas y recursos UI, usar Cache-First con Network Fallback
    event.respondWith(
        caches.match(event.request).then((response) => {
            return response || fetch(event.request).then((fetchResponse) => {
                return caches.open(CACHE_NAME).then((cache) => {
                    if (event.request.method === 'GET' && fetchResponse.status === 200) {
                        cache.put(event.request, fetchResponse.clone());
                    }
                    return fetchResponse;
                });
            });
        }).catch(() => {
            if (event.request.mode === 'navigate') {
                return caches.match('/pos/');
            }
        })
    );
});
