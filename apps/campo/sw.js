/* Service worker: la app abre siempre, con o sin señal (cache-first del shell). */
// Subir esta versión en CADA cambio del shell: la estrategia es cache-first,
// así que sin bump el navegador sigue sirviendo el app.js viejo para siempre.
const CACHE = "campo-v3";
const SHELL = ["./", "index.html", "config.js", "app.js", "manifest.json"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL))));
self.addEventListener("activate", (e) =>
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))));
self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  e.respondWith(caches.match(e.request).then((hit) => hit || fetch(e.request)));
});
