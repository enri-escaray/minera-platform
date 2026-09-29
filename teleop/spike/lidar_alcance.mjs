// Prueba de concepto (Fase 0): alcance y costo del LiDAR simulado.
// Uso:  node teleop/spike/lidar_alcance.mjs
import { crearMundo, hundimiento, alturaReal } from "../web/nucleo/mundo.js";
import { escanear, clasificar, CONFIG_LIDAR } from "../web/nucleo/lidar.js";

const pose = (m) => ({ x: 0, z: 0, y: alturaReal(m, 0, 0), rumbo: 0 });

function detecta(m, clase) {
  const p = pose(m);
  return clasificar(escanear(m, p), p).find((d) => d.clase === clase && d.en_corredor);
}

// "Continuo" = la mayor distancia hasta la cual se detecta en TODAS las distancias probadas.
console.log("Hundimiento 3 x 1 m (3 m de largo, 3 m de ancho, 1 m de profundidad), borde cercano a:");
let alcanceHundimiento = 0, continuoHundimiento = 0, cortado = false;
for (let d = 10; d <= 55; d += 2.5) {
  const ok = !!detecta(crearMundo({ deformaciones: [hundimiento(d + 1.5, 0, 3, 3, 1)] }), "deformacion");
  if (ok) alcanceHundimiento = d;
  if (ok && !cortado) continuoHundimiento = d; else cortado = true;
  console.log(`  ${String(d).padStart(5)} m  ${ok ? "detectado" : "NO"}`);
}

console.log("Roca de 0,5 m de radio en el corredor, a:");
let alcanceRoca = 0, continuoRoca = 0;
cortado = false;
for (let d = 10; d <= 60; d += 5) {
  const ok = !!detecta(crearMundo({ objetos: [{ tipo: "roca", x: d, z: 0.5, radio: 0.5 }] }), "obstaculo");
  if (ok) alcanceRoca = d;
  if (ok && !cortado) continuoRoca = d; else cortado = true;
  console.log(`  ${String(d).padStart(5)} m  ${ok ? "detectada" : "NO"}`);
}

// Falsos positivos: 40 barridos sobre caminos sin nada, con pendientes distintas
let falsos = 0;
for (let i = 0; i < 40; i++) {
  const m = crearMundo({ terreno: { pendiente: (i % 5) * 0.025 } });
  const p = pose(m);
  falsos += clasificar(escanear(m, p), p).length;
}

const m = crearMundo({ deformaciones: [hundimiento(26.5, 0, 3, 3, 1)], objetos: [{ tipo: "roca", x: 40, z: 2, radio: 0.8 }] });
const p = pose(m);
const t0 = performance.now();
const N = 20;
for (let i = 0; i < N; i++) clasificar(escanear(m, p), p);
const ms = (performance.now() - t0) / N;
const rayos = CONFIG_LIDAR.elevaciones.length * ((CONFIG_LIDAR.azimutMax - CONFIG_LIDAR.azimutMin) / CONFIG_LIDAR.pasoAzimut + 1);

console.log(JSON.stringify({
  hundimiento_continuo_hasta_m: continuoHundimiento,
  hundimiento_maximo_aislado_m: alcanceHundimiento,
  roca_0_5m_continuo_hasta_m: continuoRoca,
  roca_0_5m_maximo_m: alcanceRoca,
  falsos_positivos_en_40_barridos: falsos,
  rayos_por_barrido: rayos,
  ms_por_barrido: +ms.toFixed(1),
  barridos_por_segundo_max: +(1000 / ms).toFixed(1),
}, null, 2));
