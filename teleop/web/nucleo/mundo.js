// Mundo simulado del rajo: terreno planificado, terreno real y objetos.
//
// Lo comparten el render (three.js) y el LiDAR simulado, pero NO depende de
// three.js: es JavaScript puro para poder probarlo con `node --test`.
//
// Ejes: x = avance a lo largo del camino, z = lateral (positivo a la derecha
// del sentido de avance), y = altura. Metros.

/** Terreno según el plan de mina: camino con pendiente y bermas a los costados. */
export function alturaPlanificada(cfg, x, z) {
  const { pendiente = 0, anchoCamino = 30, alturaBerma = 2, anchoBerma = 4 } = cfg;
  const base = pendiente * Math.max(0, x);
  const borde = anchoCamino / 2;
  const fuera = Math.abs(z) - borde;
  if (fuera <= 0) return base;
  // la berma sube y baja en `anchoBerma` metros (perfil triangular)
  const t = Math.min(fuera, anchoBerma) / anchoBerma;
  return base + alturaBerma * (t < 0.5 ? t * 2 : (1 - t) * 2);
}

/** Terreno real = planificado + deformaciones (hundimientos, grietas, derrumbes). */
export function alturaReal(mundo, x, z) {
  let y = alturaPlanificada(mundo.terreno, x, z);
  for (const d of mundo.deformaciones) {
    if (x >= d.x0 && x <= d.x1 && z >= d.z0 && z <= d.z1) y -= d.profundidad;
  }
  return y;
}

/**
 * @param {object} opciones
 * @param {object} [opciones.terreno] pendiente, anchoCamino, alturaBerma, anchoBerma
 * @param {Array}  [opciones.deformaciones] {x0,x1,z0,z1,profundidad,sub}
 * @param {Array}  [opciones.objetos] {tipo:'roca',x,z,radio} | {tipo:'persona',x,z,radio,alto}
 */
export function crearMundo({ terreno = {}, deformaciones = [], objetos = [] } = {}) {
  return { terreno: { pendiente: 0, anchoCamino: 30, alturaBerma: 2, anchoBerma: 4, ...terreno }, deformaciones, objetos };
}

/** Hundimiento rectangular centrado en (x, z): largo en x, ancho en z. */
export function hundimiento(x, z, largo, ancho, profundidad) {
  return { x0: x - largo / 2, x1: x + largo / 2, z0: z - ancho / 2, z1: z + ancho / 2, profundidad, sub: "hundimiento" };
}

/**
 * Distancia a la que un rayo toca algo, o null si no toca nada dentro del alcance.
 * `soloPlan` usa el terreno planificado y sin objetos: es lo que el sensor
 * *debería* ver si el camino estuviera como en el plan.
 */
export function trazarRayo(mundo, origen, dir, alcance, paso, soloPlan = false) {
  const altura = soloPlan
    ? (x, z) => alturaPlanificada(mundo.terreno, x, z)
    : (x, z) => alturaReal(mundo, x, z);
  let tPrev = 0;
  for (let t = paso; t <= alcance; t += paso) {
    const x = origen.x + dir.x * t, y = origen.y + dir.y * t, z = origen.z + dir.z * t;
    if (!soloPlan) {
      for (const o of mundo.objetos) {
        const base = alturaPlanificada(mundo.terreno, o.x, o.z);
        if (o.tipo === "roca") {
          const cy = base + o.radio * 0.6; // apoyada y algo enterrada
          if ((x - o.x) ** 2 + (y - cy) ** 2 + (z - o.z) ** 2 <= o.radio ** 2) return { t, x, y, z, objeto: o };
        } else if ((x - o.x) ** 2 + (z - o.z) ** 2 <= o.radio ** 2 && y >= base && y <= base + o.alto) {
          return { t, x, y, z, objeto: o };
        }
      }
    }
    if (y <= altura(x, z)) {
      // refina el cruce con el suelo por bisección entre el paso anterior y este
      let a = tPrev, b = t;
      for (let i = 0; i < 12; i++) {
        const m = (a + b) / 2;
        if (origen.y + dir.y * m <= altura(origen.x + dir.x * m, origen.z + dir.z * m)) b = m; else a = m;
      }
      return { t: b, x: origen.x + dir.x * b, y: origen.y + dir.y * b, z: origen.z + dir.z * b, objeto: null };
    }
    tPrev = t;
  }
  return null;
}
