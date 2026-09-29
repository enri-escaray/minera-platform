// Pruebas del LiDAR simulado (Fase 0, punto de control 0).
// Correr con:  node --test teleop/web/nucleo/tests
import { test } from "node:test";
import assert from "node:assert/strict";
import { crearMundo, hundimiento, alturaReal } from "../mundo.js";
import { escanear, clasificar } from "../lidar.js";

const pose = (mundo, x = 0, z = 0) => ({ x, z, y: alturaReal(mundo, x, z), rumbo: 0 });
const detectar = (mundo, p = pose(mundo)) => clasificar(escanear(mundo, p), p);

test("camino plano y vacío: ninguna detección (sin falsos positivos)", () => {
  const m = crearMundo();
  assert.deepEqual(detectar(m), []);
});

test("rampa del 8 % que está en el plan: ninguna detección", () => {
  const m = crearMundo({ terreno: { pendiente: 0.08 } });
  assert.deepEqual(detectar(m), []);
});

test("hundimiento de 3 x 1 m a 25 m en el corredor: se detecta como deformación", () => {
  const m = crearMundo({ deformaciones: [hundimiento(26.5, 0, 3, 3, 1)] });
  const dets = detectar(m);
  const d = dets.find((x) => x.clase === "deformacion");
  assert.ok(d, `sin deformación en ${JSON.stringify(dets)}`);
  assert.ok(d.en_corredor);
  assert.ok(d.dist_m >= 24 && d.dist_m <= 28.5, `distancia ${d.dist_m}`);
});

test("roca de 0,8 m de radio a 30 m: obstáculo en el corredor", () => {
  const m = crearMundo({ objetos: [{ tipo: "roca", x: 30, z: 1, radio: 0.8 }] });
  const d = detectar(m).find((x) => x.clase === "obstaculo");
  assert.ok(d);
  assert.equal(d.sub, "roca_u_objeto");
  assert.ok(d.en_corredor);
  assert.ok(Math.abs(d.dist_m - 29.2) < 1.5, `distancia ${d.dist_m}`);
});

test("persona (1,8 m, angosta) a 20 m: obstáculo marcado como posible persona", () => {
  const m = crearMundo({ objetos: [{ tipo: "persona", x: 20, z: -2, radio: 0.3, alto: 1.8 }] });
  const d = detectar(m).find((x) => x.clase === "obstaculo");
  assert.ok(d);
  assert.equal(d.sub, "posible_persona");
});

test("hundimiento al costado del camino, fuera del corredor: no se marca en el corredor", () => {
  const m = crearMundo({ deformaciones: [hundimiento(20, 13, 3, 2, 1)] });
  const d = detectar(m).find((x) => x.clase === "deformacion");
  assert.ok(!d || !d.en_corredor);
});
