/* Slot Atlas — オフライン用サービスワーカー
   ホール現地は電波が弱いことがあるため、初回アクセス後はオフラインでも
   カレンダーを開けるようアプリ本体と暗号化データをキャッシュする。
   復号はブラウザ内でのみ行われ、パスワードはどこにも送信・保存されない。 */
"use strict";

const CACHE = "slot-atlas-0.11.29-2026-07-15";

// The app shell is small and must all be present for the page to render at
// all, so its install step is atomic (addAll fails install if any one 404s).
const SHELL_ASSETS = [
  "./",
  "./index.html",
  "./style.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/apple-touch-icon.png"
].map(p => new URL(p, self.registration.scope).toString());

// The encrypted payload is multi-MB and changes with every data update.
// Fetching it is best-effort at install time: on weak signal (the exact
// place this app is meant to be usable) a failed download here must not
// abort the whole service-worker install, or the app shell itself would
// stop updating too.
const VAULT_ASSET = new URL("./data/vault.json", self.registration.scope).toString();

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(SHELL_ASSETS).then(() =>
        c.add(VAULT_ASSET).catch(() => {})
      ))
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

  // The encrypted payload changes more frequently than the app shell, so a
  // fresh visit prefers the network copy. But it is also multi-MB, and this
  // app exists to be usable on weak hall wifi/4G -- so a slow or failed
  // network attempt falls back to whatever is cached, on a short timeout,
  // instead of leaving the page blank or hanging.
  if (url.pathname.endsWith("/data/vault.json")) {
    e.respondWith(
      caches.match(req).then(cached => {
        const network = fetch(req, { cache: "no-store" }).then(res => {
          const copy = res.clone();
          caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
          return res;
        });
        if (!cached) return network.catch(() => { throw new Error("vault fetch failed and nothing cached yet"); });
        const timeout = new Promise(resolve => setTimeout(() => resolve(cached), 4000));
        return Promise.race([network.catch(() => cached), timeout]);
      })
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
