/* Slot Atlas — オフライン用サービスワーカー
   ホール現地は電波が弱いことがあるため、初回アクセス後はオフラインでも
   カレンダーを開けるようアプリ本体とデータをキャッシュする。 */
"use strict";

const CACHE = "slot-atlas-v1";

const ASSETS = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./data/candidates.json",
  "./data/meta.json",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png"
].map(p => new URL(p, self.registration.scope).toString());

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ナビゲーション（ページ遷移）はネットワーク優先→失敗時キャッシュ。
// それ以外の同一オリジン資産はキャッシュ優先→ネットワーク補完。
self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  if (req.mode === "navigate") {
    e.respondWith(
      fetch(req)
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req).then(r => r || caches.match(new URL("./index.html", self.registration.scope).toString())))
    );
    return;
  }

  if (new URL(req.url).origin !== self.location.origin) return;

  e.respondWith(
    caches.match(req).then(cached => {
      if (cached) return cached;
      return fetch(req).then(res => {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => cached);
    })
  );
});
