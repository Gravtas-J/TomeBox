self.addEventListener('install', (event) => {
    self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
    // Satisfy Chrome's requirement by actively intercepting the fetch
    event.respondWith(fetch(event.request).catch(() => {
        return new Response("App is offline");
    }));
});