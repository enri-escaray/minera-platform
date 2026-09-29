// Prueba de concepto (Fase 0): ¿COCO-SSD reconoce personas dentro del rajo simulado?
//
// Mide, por distancia y con/sin niebla, la tasa de detección en 4 variantes:
//   A 512x288, B 384x216, C 256x144 (escalones de video) y R = recorte central
//   del render (480x270, equivale a acercar 2x el centro de la imagen).
// Además: falsos positivos (cuadros sin persona), ms por detección, cuadros por
// segundo del render y peso del JPEG de cada escalón (para la prueba de enlace).
//
// Las personas salen de las fotos de teleop/web/maquina/personas/*.png (fuera de
// git). Si no hay fotos, usa una figura dibujada: sirve para probar el circuito,
// pero su tasa de detección NO representa a las fotos reales.
import * as THREE from "three";
import { crearMundo, alturaReal, alturaPlanificada } from "../web/nucleo/mundo.js";

const ANCHO = 960, ALTO = 540, FOV_H = 70, ALTURA_CAMARA = 3.0;
const UMBRAL = 0.35;
const DISTANCIAS = [5, 10, 15, 20, 25, 30, 35, 40];
const LATERALES = [0, 3];
const CUADROS = 4;
const VARIANTES = {
  A: { w: 512, h: 288, q: 0.6 },
  B: { w: 384, h: 216, q: 0.5 },
  C: { w: 256, h: 144, q: 0.4 },
  R: { w: 480, h: 270, recorte: true },
};
const $ = (id) => document.getElementById(id);

// ---------- mundo y escena ----------
const mundo = crearMundo({
  objetos: [
    { tipo: "roca", x: 18, z: 9, radio: 0.9 }, { tipo: "roca", x: 33, z: -8, radio: 1.2 },
    { tipo: "roca", x: 52, z: 5, radio: 0.7 }, { tipo: "roca", x: 70, z: -11, radio: 1.5 },
  ],
});
const renderer = new THREE.WebGLRenderer({ canvas: $("vista"), antialias: false, preserveDrawingBuffer: true });
renderer.setPixelRatio(1);
renderer.setSize(ANCHO, ALTO, false);
const escena = new THREE.Scene();
const CIELO = new THREE.Color("#B9C3CF");
escena.background = CIELO;
const fovV = (2 * Math.atan(Math.tan((FOV_H * Math.PI) / 360) * (ALTO / ANCHO)) * 180) / Math.PI;
const camara = new THREE.PerspectiveCamera(fovV, ANCHO / ALTO, 0.3, 500);
escena.add(new THREE.HemisphereLight(0xe6ecf2, 0x7d6a55, 1.2));
const sol = new THREE.DirectionalLight(0xffffff, 1.7);
sol.position.set(-40, 80, 30);
escena.add(sol);

function terreno() {
  const x0 = -20, x1 = 200, z0 = -45, z1 = 45, paso = 1;
  const nx = (x1 - x0) / paso, nz = (z1 - z0) / paso;
  const pos = [], col = [], idx = [];
  const camino = new THREE.Color("#8E7B63"), berma = new THREE.Color("#A28E72"), fuera = new THREE.Color("#9A8A74");
  let semilla = 7;
  const azar = () => ((semilla = (semilla * 16807) % 2147483647) / 2147483647);
  for (let i = 0; i <= nx; i++) for (let j = 0; j <= nz; j++) {
    const x = x0 + i * paso, z = z0 + j * paso;
    pos.push(x, alturaReal(mundo, x, z), z);
    const base = Math.abs(z) <= mundo.terreno.anchoCamino / 2 ? camino : Math.abs(z) <= mundo.terreno.anchoCamino / 2 + mundo.terreno.anchoBerma ? berma : fuera;
    const k = 0.9 + azar() * 0.2; // variación de tono: el suelo real no es liso
    col.push(base.r * k, base.g * k, base.b * k);
  }
  for (let i = 0; i < nx; i++) for (let j = 0; j < nz; j++) {
    const a = i * (nz + 1) + j, b = a + 1, c = a + (nz + 1), d = c + 1;
    idx.push(a, b, c, b, d, c);
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  g.setIndex(idx);
  g.computeVertexNormals();
  escena.add(new THREE.Mesh(g, new THREE.MeshLambertMaterial({ vertexColors: true })));
}
terreno();

for (const o of mundo.objetos) {
  const m = new THREE.Mesh(new THREE.IcosahedronGeometry(o.radio, 0), new THREE.MeshLambertMaterial({ color: 0x7c7266, flatShading: true }));
  m.position.set(o.x, alturaPlanificada(mundo.terreno, o.x, o.z) + o.radio * 0.6, o.z);
  escena.add(m);
}
// un camión de acarreo estacionado (distractor: no es una persona)
{
  const g = new THREE.Group();
  const caja = new THREE.Mesh(new THREE.BoxGeometry(9, 4, 7), new THREE.MeshLambertMaterial({ color: 0xd9a21b }));
  caja.position.y = 4;
  const cabina = new THREE.Mesh(new THREE.BoxGeometry(2.5, 2.5, 3), new THREE.MeshLambertMaterial({ color: 0x3a3f48 }));
  cabina.position.set(4.5, 5, -2);
  g.add(caja, cabina);
  for (const [x, z] of [[-3, -3.5], [-3, 3.5], [3, -3.5], [3, 3.5]]) {
    const r = new THREE.Mesh(new THREE.CylinderGeometry(1.8, 1.8, 1.2, 16), new THREE.MeshLambertMaterial({ color: 0x22262e }));
    r.rotation.x = Math.PI / 2;
    r.position.set(x, 1.8, z);
    g.add(r);
  }
  g.position.set(60, 0, -10);
  escena.add(g);
}

// ---------- personas (fotos o figura dibujada) ----------
async function listarFotos() {
  try {
    const html = await (await fetch("../web/maquina/personas/")).text();
    return [...html.matchAll(/href="([^"]+\.png)"/gi)].map((m) => "../web/maquina/personas/" + m[1]);
  } catch { return []; }
}

function figuraDibujada() {
  const c = document.createElement("canvas");
  c.width = 256; c.height = 640;
  const g = c.getContext("2d");
  g.fillStyle = "#e0b491"; g.beginPath(); g.arc(128, 70, 42, 0, Math.PI * 2); g.fill();       // cabeza
  g.fillStyle = "#f2c200"; g.beginPath(); g.ellipse(128, 40, 50, 26, 0, Math.PI, 0); g.fill(); // casco
  g.fillStyle = "#f26b1d"; g.fillRect(70, 118, 116, 190);                                      // chaleco
  g.fillStyle = "#dcdcdc"; g.fillRect(70, 200, 116, 14); g.fillRect(70, 250, 116, 14);          // bandas
  g.fillStyle = "#f26b1d"; g.fillRect(34, 124, 34, 170); g.fillRect(188, 124, 34, 170);         // brazos
  g.fillStyle = "#e0b491"; g.fillRect(36, 290, 30, 34); g.fillRect(190, 290, 30, 34);          // manos
  g.fillStyle = "#2f4a78"; g.fillRect(74, 308, 50, 290); g.fillRect(132, 308, 50, 290);         // piernas
  g.fillStyle = "#3a2c22"; g.fillRect(66, 596, 60, 30); g.fillRect(130, 596, 60, 30);           // botines
  return c;
}

async function crearPersonas() {
  const fotos = await listarFotos();
  const cargar = (url) => new Promise((ok, mal) => new THREE.TextureLoader().load(url, ok, undefined, mal));
  const texturas = fotos.length
    ? await Promise.all(fotos.slice(0, 6).map(cargar))
    : [new THREE.CanvasTexture(figuraDibujada())];
  return {
    fuente: fotos.length ? `${Math.min(fotos.length, 6)} foto(s) de personas/` : "figura dibujada (sin fotos todavía)",
    mallas: texturas.map((t) => {
      t.colorSpace = THREE.SRGBColorSpace;
      const img = t.image, alto = 1.75, ancho = (alto * img.width) / img.height;
      const m = new THREE.Mesh(new THREE.PlaneGeometry(ancho, alto), new THREE.MeshBasicMaterial({ map: t, transparent: true, alphaTest: 0.5 }));
      m.geometry.translate(0, alto / 2, 0);
      m.visible = false;
      escena.add(m);
      return m;
    }),
  };
}

// ---------- cámara, render y variantes ----------
function ubicarCamara(dx = 0, yaw = 0) {
  camara.position.set(dx, alturaReal(mundo, dx, 0) + ALTURA_CAMARA, 0);
  const a = (yaw * Math.PI) / 180;
  camara.lookAt(dx + 100 * Math.cos(a), camara.position.y - 4, 100 * Math.sin(a));
}

function niebla(on) { escena.fog = on ? new THREE.Fog(CIELO, 8, 70) : null; }

const lienzos = {};
for (const [k, v] of Object.entries(VARIANTES)) {
  lienzos[k] = document.createElement("canvas");
  lienzos[k].width = v.w; lienzos[k].height = v.h;
}
function variante(k) {
  const v = VARIANTES[k], c = lienzos[k], g = c.getContext("2d");
  if (v.recorte) g.drawImage($("vista"), (ANCHO - v.w) / 2, (ALTO - v.h) / 2, v.w, v.h, 0, 0, v.w, v.h);
  else g.drawImage($("vista"), 0, 0, v.w, v.h);
  return c;
}

/** Caja de la persona en píxeles de la variante `k` (para saber si el detector acertó). */
function cajaProyectada(malla, k) {
  const v = VARIANTES[k], p = new THREE.Box3().setFromObject(malla);
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const x of [p.min.x, p.max.x]) for (const y of [p.min.y, p.max.y]) for (const z of [p.min.z, p.max.z]) {
    const s = new THREE.Vector3(x, y, z).project(camara);
    const px = ((s.x + 1) / 2) * ANCHO, py = ((1 - s.y) / 2) * ALTO;
    x0 = Math.min(x0, px); x1 = Math.max(x1, px); y0 = Math.min(y0, py); y1 = Math.max(y1, py);
  }
  if (v.recorte) { const ox = (ANCHO - v.w) / 2, oy = (ALTO - v.h) / 2; return [x0 - ox, y0 - oy, x1 - ox, y1 - oy]; }
  const sx = v.w / ANCHO, sy = v.h / ALTO;
  return [x0 * sx, y0 * sy, x1 * sx, y1 * sy];
}

function acierta(dets, caja) {
  const [x0, y0, x1, y1] = caja, mx = (x1 - x0) * 0.3, my = (y1 - y0) * 0.3;
  return dets.some((d) => {
    if (d.class !== "person" || d.score < UMBRAL) return false;
    const cx = d.bbox[0] + d.bbox[2] / 2, cy = d.bbox[1] + d.bbox[3] / 2;
    return cx >= x0 - mx && cx <= x1 + mx && cy >= y0 - my && cy <= y1 + my;
  });
}

// Cede el control sin depender de requestAnimationFrame: si el panel del navegador
// está oculto, rAF deja de dispararse y la medición se quedaría esperando.
const esperar = () => new Promise((r) => setTimeout(r, 0));
const bytesJpeg = (c, q) => new Promise((r) => c.toBlob((b) => r(b.size), "image/jpeg", q));

/** ms por cuadro de render, esperando a la GPU (gl.finish): no depende de que el panel esté visible. */
function msRender(n = 60) {
  const gl = renderer.getContext();
  const t0 = performance.now();
  for (let i = 0; i < n; i++) {
    ubicarCamara(Math.sin(i / 7), Math.sin(i / 11));
    renderer.render(escena, camara);
    gl.finish();
  }
  return (performance.now() - t0) / n;
}

// ---------- medición ----------
async function medir(modelo, personas) {
  $("medir").disabled = true;
  const res = {
    fecha: new Date().toISOString(), backend: tf.getBackend(), fuente_personas: personas.fuente,
    umbral: UMBRAL, cuadros_por_caso: CUADROS, render: `${ANCHO}x${ALTO}`, fov_horizontal: FOV_H, altura_camara_m: ALTURA_CAMARA,
    tasa: {}, falsos_positivos_por_100: {}, ms_por_deteccion: {}, jpeg_bytes: {},
  };
  const ms = Object.fromEntries(Object.keys(VARIANTES).map((k) => [k, []]));
  let semilla = 11;
  const azar = () => ((semilla = (semilla * 16807) % 2147483647) / 2147483647) - 0.5;

  for (const conNiebla of [false, true]) {
    niebla(conNiebla);
    const etiqueta = conNiebla ? "con_niebla" : "sin_niebla";
    for (const d of DISTANCIAS) {
      const aciertos = Object.fromEntries(Object.keys(VARIANTES).map((k) => [k, 0]));
      let casos = 0;
      for (const malla of personas.mallas) for (const lat of LATERALES) for (let f = 0; f < CUADROS; f++) {
        personas.mallas.forEach((m) => (m.visible = m === malla));
        const dx = azar(), yaw = azar() * 1.6;
        malla.position.set(dx + d, alturaReal(mundo, dx + d, lat), lat);
        ubicarCamara(dx, yaw);
        malla.lookAt(camara.position.x, malla.position.y, camara.position.z);
        renderer.render(escena, camara);
        await esperar();
        for (const k of Object.keys(VARIANTES)) {
          const t0 = performance.now();
          const dets = await modelo.detect(variante(k), 20, 0.2);
          ms[k].push(performance.now() - t0);
          if (acierta(dets, cajaProyectada(malla, k))) aciertos[k]++;
        }
        casos++;
        $("estado").textContent = `midiendo: ${etiqueta}, ${d} m (${casos} casos)`;
      }
      for (const k of Object.keys(VARIANTES)) {
        res.tasa[k] ??= {};
        res.tasa[k][d] ??= {};
        res.tasa[k][d][etiqueta] = +(aciertos[k] / casos).toFixed(2);
      }
    }
    // falsos positivos: los mismos encuadres sin ninguna persona
    personas.mallas.forEach((m) => (m.visible = false));
    const fp = Object.fromEntries(Object.keys(VARIANTES).map((k) => [k, 0]));
    let n = 0;
    for (let i = 0; i < 40; i++) {
      ubicarCamara(azar() * 60 + 30, azar() * 40);
      renderer.render(escena, camara);
      await esperar();
      for (const k of Object.keys(VARIANTES)) {
        const dets = await modelo.detect(variante(k), 20, 0.2);
        fp[k] += dets.filter((x) => x.class === "person" && x.score >= UMBRAL).length;
      }
      n++;
    }
    for (const k of Object.keys(VARIANTES)) {
      res.falsos_positivos_por_100[k] ??= {};
      res.falsos_positivos_por_100[k][etiqueta] = +((100 * fp[k]) / n).toFixed(1);
    }
  }
  niebla(false);
  for (const k of Object.keys(VARIANTES)) {
    const v = [...ms[k]].sort((a, b) => a - b);
    res.ms_por_deteccion[k] = { mediana: +v[Math.floor(v.length / 2)].toFixed(1), p90: +v[Math.floor(v.length * 0.9)].toFixed(1) };
  }
  // peso del JPEG de cada escalón de video (promedio de 10 encuadres)
  for (const k of ["A", "B", "C"]) {
    let suma = 0;
    for (let i = 0; i < 10; i++) {
      ubicarCamara(i * 3, (i % 3) - 1);
      renderer.render(escena, camara);
      await esperar();
      suma += await bytesJpeg(variante(k), VARIANTES[k].q);
    }
    res.jpeg_bytes[k] = Math.round(suma / 10);
  }
  const msR = msRender();
  res.ms_por_render = +msR.toFixed(1);
  res.fps_render_max = +(1000 / msR).toFixed(1);
  $("resultados").textContent = JSON.stringify(res, null, 2);
  console.log("RESULTADOS_CAMARA " + JSON.stringify(res));
  $("estado").textContent = "listo";
  $("medir").disabled = false;
  return res;
}

// ---------- inicio ----------
const personas = await crearPersonas();
$("estado").textContent = `cargando modelo (${personas.fuente})…`;
const t0 = performance.now();
const modelo = await cocoSsd.load({ base: "lite_mobilenet_v2", modelUrl: "../web/vendor/modelos/ssdlite_mobilenet_v2/model.json" });
await modelo.detect(variante("A")); // precalentamiento (compila los shaders de WebGL)
const cargaMs = Math.round(performance.now() - t0);

function vistaPrevia() {
  const d = +$("dist").value;
  $("distTxt").textContent = `${d} m`;
  niebla($("niebla").checked);
  personas.mallas.forEach((m, i) => (m.visible = i === 0));
  const m = personas.mallas[0];
  m.position.set(d, alturaReal(mundo, d, 0), 0);
  ubicarCamara(0, 0);
  m.lookAt(camara.position.x, m.position.y, camara.position.z);
  renderer.render(escena, camara);
}
$("dist").oninput = vistaPrevia;
$("niebla").onchange = vistaPrevia;
$("medir").onclick = () => medir(modelo, personas);
vistaPrevia();
$("estado").textContent = `listo · backend ${tf.getBackend()} · modelo cargado en ${cargaMs} ms · personas: ${personas.fuente}`;
$("medir").disabled = false;
window.__medir = () => medir(modelo, personas); // para lanzar la medición desde las herramientas de prueba
