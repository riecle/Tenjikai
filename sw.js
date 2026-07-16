/* Slot Atlas — オフライン用サービスワーカー
   ホール現地は電波が弱いことがあるため、初回アクセス後はオフラインでも
   カレンダーを開けるようアプリ本体と暗号化データをキャッシュする。
   復号はブラウザ内でのみ行われ、パスワードはどこにも送信・保存されない。 */
"use strict";

const CACHE = "slot-atlas-0.11.29-2026-07-15";

const ASSETS = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.webmanifest",
  "./data/vault.json",
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

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // The encrypted payload changes more frequently than the app shell. Always
  // try the network first so a data-only deployment does not remain hidden by
  // an old service-worker cache; fall back to the last usable vault offline.
  if (url.pathname.endsWith("/data/vault.json")) {
    e.respondWith(
      fetch(req, { cache: "no-store" })
        .then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

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
