/* Panel de sitio, solo lectura: consulta las APIs de los cuatro servicios y se
 * refresca solo. No escribe nada: las acciones siguen viviendo en cada servicio
 * con su RBAC. Si un servicio no responde, su tarjeta lo dice y el resto sigue. */
const URLS = window.PANEL_URLS || {};
const REFRESCO_MS = window.PANEL_REFRESCO_MS || 10000;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function chip(valor) {
  const conocidos = ["abierta", "agendada", "cerrada", "disponible", "en_ciclo", "mantenimiento", "activo"];
  const clase = conocidos.includes(valor) ? valor : "otro";
  return `<span class="chip ${clase}">${esc(valor)}</span>`;
}

function hora(iso) {
  return iso ? new Date(iso).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" }) : "—";
}

async function consultar(servicio, ruta) {
  const r = await fetch(URLS[servicio] + ruta);
  if (!r.ok) throw new Error(`${servicio} respondió ${r.status}`);
  return r.json();
}

function tabla(columnas, filas) {
  if (!filas.length) return `<p class="vacio">Sin datos todavía.</p>`;
  return `<table><thead><tr>${columnas.map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead>` +
    `<tbody>${filas.map((f) => `<tr>${f.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

/* --- Despacho: palas, camiones y asignaciones vivas con su motivo --- */
async function pintarDespacho() {
  const e = await consultar("despacho", "/estado");
  const kpis = `<div class="kpis">
    <div class="kpi"><strong>${e.palas.filter((p) => p.activa).length}</strong><span>palas activas</span></div>
    <div class="kpi"><strong>${e.camiones.length}</strong><span>camiones</span></div>
    <div class="kpi"><strong>${e.asignaciones_vivas}</strong><span>asignaciones vivas</span></div>
  </div>`;
  const camiones = tabla(["Camión", "Capacidad", "Estado"],
    e.camiones.map((c) => [esc(c.id), `${c.capacidad_ton} t`, chip(c.estado)]));
  const asignaciones = (e.asignaciones || []).map((a) =>
    [esc(a.camion_id), esc(a.pala_id), `${a.espera_estimada_min.toFixed(1)} min`,
     `<span class="motivo">${esc(a.motivo)}</span>`]);
  $("despacho").innerHTML = kpis + camiones +
    (asignaciones.length
      ? `<h2 style="margin-top:12px">Asignaciones vivas</h2>` +
        tabla(["Camión", "Pala", "Espera est.", "Motivo (explicable)"], asignaciones)
      : "");
}

/* --- Mantenimiento: OTs con prioridad y estado --- */
async function pintarOTs() {
  const ots = await consultar("mantenimiento", "/ots");
  ots.sort((a, b) => (b.creada_en || "").localeCompare(a.creada_en || ""));
  $("ots").innerHTML = tabla(
    ["Equipo", "Diagnóstico", "Prioridad", "Estado", "Creada"],
    ots.slice(0, 20).map((ot) => [
      esc(ot.equipo_id), esc(ot.diagnostico), esc(ot.prioridad), chip(ot.estado), hora(ot.creada_en),
    ]));
}

/* --- Trazabilidad: lotes y, al elegir uno, su cadena de custodia --- */
function nodoCustodia(c) {
  const pesajes = c.pesajes.map((p) =>
    `<div>${hora(p.medido_en)} · ${esc(p.instrumento_id)} · ${(p.masa_kg / 1000).toFixed(2)} t</div>`).join("");
  const origenes = c.origenes.map((o) => `<div class="nodo">${nodoCustodia(o)}</div>`).join("");
  return `<strong>${esc(c.lote.codigo)}</strong> ${chip(c.lote.estado)} · ${esc(c.lote.tipo)}` +
    ` · total ${(c.masa_total_kg / 1000).toFixed(2)} t${pesajes}${origenes}`;
}

async function verCustodia(loteId) {
  try {
    const c = await consultar("trazabilidad", `/lotes/${loteId}/custodia`);
    $("custodia").innerHTML = `<h2 style="margin-top:12px">Cadena de custodia</h2><div class="nodo">${nodoCustodia(c)}</div>`;
  } catch (err) {
    $("custodia").innerHTML = `<p class="error">${esc(err.message)}</p>`;
  }
}

async function pintarLotes() {
  const lotes = await consultar("trazabilidad", "/lotes");
  $("lotes").innerHTML = tabla(
    ["Código", "Tipo", "Estado", "Creado"],
    lotes.slice(0, 15).map((l) => [
      `<button class="enlace" data-lote="${esc(l.id)}">${esc(l.codigo)}</button>`,
      esc(l.tipo), chip(l.estado), hora(l.creado_en),
    ]));
  $("lotes").querySelectorAll("button[data-lote]").forEach((b) =>
    b.addEventListener("click", () => verCustodia(b.dataset.lote)));
}

/* --- Reporte del turno en curso (P2 bajo demanda) --- */
function turnoActual() {
  const ahora = new Date();
  const h = ahora.getUTCHours(); // los turnos del scheduler corren en UTC (pendiente: hora local del sitio)
  return { fecha: ahora.toISOString().slice(0, 10), turno: h >= 7 && h < 19 ? "dia" : "noche" };
}

async function pintarTurno() {
  const { fecha, turno } = turnoActual();
  const r = await consultar("reportes", `/reportes/turno?fecha=${fecha}&turno=${turno}`);
  const kpis = `<div class="kpis">
    <div class="kpi"><strong>${r.total_toneladas.toFixed(1)}</strong><span>toneladas del turno</span></div>
    <div class="kpi"><strong>${r.cantidad_pesajes}</strong><span>pesajes</span></div>
    <div class="kpi"><strong>${r.novedades.length}</strong><span>novedades</span></div>
  </div><p class="vacio">Turno ${esc(turno)} · ${esc(fecha)}</p>`;
  const novedades = tabla(["Hora", "Categoría", "Novedad", "Autor"],
    r.novedades.map((n) => [hora(n.creado_en), esc(n.categoria), esc(n.texto), esc(n.autor)]));
  $("turno").innerHTML = kpis + novedades;
}

/* --- Ciclo de refresco: cada tarjeta falla sola, el panel nunca se cae --- */
const TARJETAS = [
  ["despacho", pintarDespacho],
  ["ots", pintarOTs],
  ["lotes", pintarLotes],
  ["turno", pintarTurno],
];

async function refrescar() {
  const salud = [];
  for (const [id, pintar] of TARJETAS) {
    try {
      await pintar();
      salud.push(true);
    } catch (err) {
      $(id).innerHTML = `<p class="error">Sin conexión con el servicio: ${esc(err.message)}</p>`;
      salud.push(false);
    }
  }
  const nombres = ["despacho", "mantenimiento", "trazabilidad", "reportes"];
  $("servicios").innerHTML = nombres.map((n, i) =>
    `<span class="svc ${salud[i] ? "arriba" : "abajo"}">${n}</span>`).join("");
  $("actualizado").textContent = "actualizado " + new Date().toLocaleTimeString("es");
}

refrescar();
setInterval(refrescar, REFRESCO_MS);
