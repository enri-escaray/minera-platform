// LiDAR simulado a bordo: barre un abanico de rayos y clasifica lo que encuentra.
//
// Solo recibe distancias (como un sensor real): la clasificación compara cada
// medición con lo que *debería* medir si el camino estuviera como en el plan.
//   - Objeto por encima del suelo planificado -> "obstaculo"
//     (si es alto y angosto: sub "posible_persona", respaldo de la cámara con niebla).
//   - Suelo más lejos o más bajo de lo esperado -> "deformacion" (hundimiento,
//     grieta, borde caído). Desde lejos el rayo no ve el fondo del pozo, pero
//     sí mide de más: el suelo "no está" donde debería.

import { trazarRayo } from "./mundo.js";

export const CONFIG_LIDAR = {
  alturaSensor: 2.5,      // m sobre el camino (frente del camión)
  azimutMin: -30, azimutMax: 30, pasoAzimut: 1, // grados
  elevaciones: rango(-2, -15, -0.5),             // grados (negativos: hacia abajo)
  alcance: 60, paso: 0.25,                       // m
  excesoRango: 1.0,       // m de más respecto de lo esperado -> falta suelo
  hundido: 0.4,           // m por debajo del plan -> depresión
  sobreSuelo: 0.3,        // m por encima del plan -> objeto
  anchoCorredor: 12,      // m: ancho del camión (~9 m) más margen
  celda: 1.5,             // m: tamaño de celda para agrupar puntos
  minPuntos: 2,           // puntos por grupo para reportar (filtra ruido)
};

function rango(desde, hasta, paso) {
  const r = [];
  for (let v = desde; paso < 0 ? v >= hasta - 1e-9 : v <= hasta + 1e-9; v += paso) r.push(+v.toFixed(3));
  return r;
}

const rad = (g) => (g * Math.PI) / 180;

/** Barre el abanico desde `pose` = {x, z, y (suelo bajo el sensor), rumbo (rad)}. */
export function escanear(mundo, pose, cfg = CONFIG_LIDAR) {
  const origen = { x: pose.x, y: pose.y + cfg.alturaSensor, z: pose.z };
  const rayos = [];
  for (const el of cfg.elevaciones) {
    for (let az = cfg.azimutMin; az <= cfg.azimutMax + 1e-9; az += cfg.pasoAzimut) {
      const a = pose.rumbo + rad(az), e = rad(el);
      const dir = { x: Math.cos(e) * Math.cos(a), y: Math.sin(e), z: Math.cos(e) * Math.sin(a) };
      const medido = trazarRayo(mundo, origen, dir, cfg.alcance, cfg.paso);
      const esperado = trazarRayo(mundo, origen, dir, cfg.alcance, cfg.paso, true);
      rayos.push({ az, el, dir, medido, esperado });
    }
  }
  return { origen, rayos };
}

/** Agrupa los puntos anómalos y devuelve detecciones ordenadas por distancia. */
export function clasificar(escaneo, pose, cfg = CONFIG_LIDAR) {
  const puntos = [];
  for (const r of escaneo.rayos) {
    const { medido, esperado } = r;
    if (medido?.objeto) {
      const alto = medido.objeto.alto ?? medido.objeto.radio * 1.6;
      if (medido.y - (esperado ? esperado.y : 0) >= cfg.sobreSuelo || alto >= cfg.sobreSuelo) {
        puntos.push({ clase: "obstaculo", x: medido.x, z: medido.z, alto, ancho: 2 * medido.objeto.radio });
      }
      continue;
    }
    if (!esperado) continue; // el rayo no debía tocar suelo dentro del alcance
    const faltaSuelo = !medido || medido.t > esperado.t + cfg.excesoRango;
    const hundido = medido && esperado.y - medido.y > cfg.hundido;
    if (faltaSuelo || hundido) puntos.push({ clase: "deformacion", x: esperado.x, z: esperado.z });
  }
  return agrupar(puntos, pose, cfg);
}

function agrupar(puntos, pose, cfg) {
  const grupos = new Map();
  for (const p of puntos) {
    const k = `${p.clase}|${Math.floor(p.x / cfg.celda)}|${Math.floor(p.z / cfg.celda)}`;
    if (!grupos.has(k)) grupos.set(k, []);
    grupos.get(k).push(p);
  }
  // une celdas vecinas de la misma clase (componentes conexas en la grilla)
  const vistos = new Set(), dets = [];
  for (const k of grupos.keys()) {
    if (vistos.has(k)) continue;
    const [clase] = k.split("|"), pila = [k], miembros = [];
    vistos.add(k);
    while (pila.length) {
      const actual = pila.pop();
      miembros.push(...grupos.get(actual));
      const [, ix, iz] = actual.split("|").map(Number);
      for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
        const v = `${clase}|${ix + dx}|${iz + dz}`;
        if (grupos.has(v) && !vistos.has(v)) { vistos.add(v); pila.push(v); }
      }
    }
    if (miembros.length < cfg.minPuntos) continue;
    dets.push(resumir(clase, miembros, pose, cfg));
  }
  return dets.sort((a, b) => a.dist_m - b.dist_m);
}

function resumir(clase, miembros, pose, cfg) {
  let cerca = null, dmin = Infinity;
  const cos = Math.cos(-pose.rumbo), sin = Math.sin(-pose.rumbo);
  let enCorredor = false;
  for (const p of miembros) {
    const dx = p.x - pose.x, dz = p.z - pose.z;
    const adelante = dx * cos - dz * sin, lateral = dx * sin + dz * cos;
    const d = Math.hypot(dx, dz);
    if (d < dmin) { dmin = d; cerca = p; }
    if (adelante > 0 && Math.abs(lateral) <= cfg.anchoCorredor / 2) enCorredor = true;
  }
  const det = { clase, fuente: "profundidad", dist_m: +dmin.toFixed(1), x: +cerca.x.toFixed(1), z: +cerca.z.toFixed(1), en_corredor: enCorredor, puntos: miembros.length };
  if (clase === "obstaculo") {
    const alto = Math.max(...miembros.map((p) => p.alto)), ancho = Math.max(...miembros.map((p) => p.ancho));
    det.sub = alto >= 1.2 && alto <= 2.3 && ancho <= 1.0 ? "posible_persona" : "roca_u_objeto";
  } else {
    det.sub = "hundimiento_o_borde";
  }
  return det;
}
