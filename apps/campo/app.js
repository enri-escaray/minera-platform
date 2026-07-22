/* App de campo offline-first (flujo P2).
 * Cada novedad se guarda primero en IndexedDB con un event_id generado en el
 * dispositivo; un sincronizador la envía a /novedades cuando hay red. El
 * servidor es idempotente por event_id: reintentar jamás duplica. */
const API = (window.CAMPO_API_URL || "") + "/novedades";
const DB_NOMBRE = "campo", STORE = "pendientes";

function abrirDB() {
  return new Promise((res, rej) => {
    const req = indexedDB.open(DB_NOMBRE, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "event_id" });
    req.onsuccess = () => res(req.result);
    req.onerror = () => rej(req.error);
  });
}

async function encolar(novedad) {
  const db = await abrirDB();
  await new Promise((res, rej) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(novedad);
    tx.oncomplete = res; tx.onerror = () => rej(tx.error);
  });
}

async function pendientes() {
  const db = await abrirDB();
  return new Promise((res) => {
    const req = db.transaction(STORE).objectStore(STORE).getAll();
    req.onsuccess = () => res(req.result);
  });
}

async function quitar(eventId) {
  const db = await abrirDB();
  await new Promise((res) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(eventId);
    tx.oncomplete = res;
  });
}

async function sincronizar() {
  const token = document.getElementById("token").value.trim();
  if (!navigator.onLine || !token) return actualizarUI();
  for (const novedad of await pendientes()) {
    try {
      const r = await fetch(API, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
        body: JSON.stringify(novedad),
      });
      if (r.ok || r.status === 409) await quitar(novedad.event_id);
      else if (r.status === 401 || r.status === 403) break; // token inválido: no insistir
    } catch { break; } // sin red real: se reintenta luego
  }
  actualizarUI();
}

async function actualizarUI() {
  const n = (await pendientes()).length;
  document.getElementById("cola").textContent =
    n ? `${n} novedad(es) esperando sincronización` : "Todo sincronizado";
  document.getElementById("conexion").textContent =
    navigator.onLine ? "En línea" : "Sin conexión: se guarda localmente";
}

document.getElementById("fecha").value = new Date().toISOString().slice(0, 10);
document.getElementById("enviar").addEventListener("click", async () => {
  const novedad = {
    event_id: crypto.randomUUID(),
    fecha: document.getElementById("fecha").value,
    turno: document.getElementById("turno").value,
    categoria: document.getElementById("categoria").value,
    texto: document.getElementById("texto").value.trim(),
  };
  if (novedad.texto.length < 3) return;
  await encolar(novedad);
  document.getElementById("texto").value = "";
  const estado = document.getElementById("estado");
  estado.hidden = false;
  estado.className = navigator.onLine ? "ok" : "pendiente";
  estado.textContent = navigator.onLine ? "Guardada, sincronizando…" : "Guardada localmente";
  sincronizar();
});
window.addEventListener("online", sincronizar);
setInterval(sincronizar, 15000);
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");
actualizarUI();
