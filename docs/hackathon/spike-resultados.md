# Pruebas de concepto de la Fase 0 · resultados

Hackatón «Hackeando la Minería», desafío 5 (teleoperación). Aporte hecho para el
concurso, posterior al tag `pre-hackaton` (base 13 de las bases).

- **Fecha:** 28/9/2026.
- **Equipo:** i5-11300H (4 núcleos), Intel Iris Xe (WebGL por ANGLE/D3D11), 7,7 GB de RAM.
- **Punto de control 0:** 4/10/2026.

Estos números alimentan el ADR-006 y la sección de límites de la entrega.

## Resumen: estado del punto de control 0

| Criterio | Meta | Resultado | Estado |
|---|---|---|---|
| Detección de personas | ≥ 90 % a ≤ 15 m; ≥ 70 % a 25 m con recorte | Sin medir con fotos reales: la figura dibujada no sirve como sujeto (ver §3) | **Pendiente**: fotos de T2 |
| Velocidad del detector | ≥ 3 detecciones por segundo | 65 ms de mediana (unas 15 por segundo), igual en las cuatro variantes | Cumple |
| Falsos positivos | ≤ 1 cada 100 cuadros | 0 en 80 cuadros sin personas (con y sin niebla), por variante | Cumple (muestra chica) |
| Render del simulador | ≥ 25 cuadros por segundo | 0,3 ms por cuadro en GPU: el render no es el cuello de botella | Cumple |
| Enlace a 64 kbps | Comandos con < 1 % de pérdida agregada, aunque se caiga el video | 100 % entregados, demora p99 de 54 ms, video en modo solo mapa | Cumple (simulación) |
| LiDAR | Hundimiento de 3 × 1 m detectado desde ≥ 25 m | Continuo hasta 30 m; roca de 0,5 m hasta 45 m; 0 falsos positivos | Cumple |
| Sin conexión | Todo carga sin internet | Todas las solicitudes van a localhost; las librerías se verifican con hash y sin red | Cumple |
| RAM libre durante la demo | ≥ 0,5 GB | 0,98 GB libres **sin Docker**, con la app de Claude (2 GB) y Chrome (0,75 GB) abiertos | **Pendiente**: medir en condiciones de demo |

## 1. Librerías sin conexión

- `tools/vendorizar_teleop.py` descarga three.js 0.186.1, TensorFlow.js 4.22.0, COCO-SSD 2.2.3 y el modelo SSDLite MobileNet v2 a `teleop/web/vendor/`.
  - **Verificación de los archivos de npm:** contra la huella SHA-256 que publica jsDelivr.
  - **Modelo y licencias:** se fijan en `vendor.lock.json` en la primera descarga.
- **Tamaño:** 22,2 MB en total. Solo el modelo pesa 18 MB (cinco archivos de pesos); se había estimado en unos 5 MB.
- **Qué va a git:** para no cargar el historial del repositorio público, solo se versionan `LICENCIAS.md` y `vendor.lock.json`. Para restaurar las librerías en otra máquina: `python tools/vendorizar_teleop.py`.
- **Verificación sin red:** `python tools/vendorizar_teleop.py --verificar` da 14 archivos OK.

## 2. LiDAR simulado (`teleop/web/nucleo/lidar.js`)

**Configuración:**
- Sensor a 2,5 m sobre el camino.
- Abanico de −30° a +30° cada 1° en horizontal y de −2° a −15° cada 0,5° en vertical: 1647 rayos.
- Alcance de 60 m.

**Clasificación:** compara lo medido con lo que *debería* medirse sobre el terreno planificado.
- Un objeto por encima del plan es un **obstáculo**. Si es alto y angosto, se marca «posible persona».
- Un suelo más lejano o más bajo de lo esperado es una **deformación**.
- Desde lejos, el láser no ve el fondo de un pozo, pero sí mide de más: el suelo «no está» donde debería.

| Prueba (`node teleop/spike/lidar_alcance.mjs`) | Resultado |
|---|---|
| Hundimiento de 3 × 1 m (1 m de profundidad) | Detectado sin cortes hasta **30 m**; intermitente entre 32,5 y 47,5 m |
| Roca de 0,5 m de radio en el corredor | Detectada hasta **45 m** |
| Falsos positivos, 40 barridos sin nada y con pendientes de 0 a 10 % | **0** |
| Costo | 33 a 42 ms por barrido en un solo hilo (unos 24 a 30 barridos por segundo como máximo) |

Pruebas automáticas: `node --test teleop/web/nucleo/tests/lidar.test.js`, 6 de 6 en verde.

**Para la Fase 1:**
- El barrido va a un Web Worker, para no competir con el render.
- Se puede cachear lo esperado cuando el terreno es uniforme.
- La intermitencia más allá de 30 m se reduce con anillos verticales más densos cerca del horizonte.

## 3. Cámara y reconocimiento (`teleop/spike/camara.html`)

**Escena:** three.js en 960 × 540, 70° de campo horizontal, cámara a 3 m. Hay rocas, un camión de acarreo estacionado a 60 m y niebla opcional (8 a 70 m).

**Detector:** COCO-SSD `lite_mobilenet_v2`, con umbral de 0,35. Se prueban cuatro variantes:
- A: 512 × 288.
- B: 384 × 216.
- C: 256 × 144.
- R: recorte central de 480 × 270, que equivale a acercar el centro de la imagen 2×.

**Qué se midió:** personas a 5, 10, 15, 20, 25, 30, 35 y 40 m, al centro y a 3 m del eje, con cuatro cuadros levemente movidos por caso.

**Resultado con la figura dibujada** (no hay fotos todavía):
- Casi no se detecta entre 5 y 25 m.
- Aparece a veces a 30 y 40 m, justo cuando se ve más borrosa.
- Nunca se detecta en el recorte central.

**Diagnóstico con umbral 0,05:** el detector ubica bien la figura, pero a 10 m la clasifica como «traffic light» (semáforo): las franjas naranja, amarilla y azul apiladas se parecen a uno. A 25 m la da como «person» con apenas 0,10 de confianza. El circuito funciona y la comparación de cajas es correcta. **La figura dibujada no representa a una persona real.** Por eso la tarea T2 (fotos con chaleco y casco) es la que destraba este criterio.

**Lo que sí queda medido:**
- **Tiempo por detección:** 65 a 67 ms de mediana en las cuatro variantes, con p90 de 95 ms en A. El modelo reescala internamente, así que la resolución de entrada casi no cambia el costo.
- **Falsos positivos:** 0 cada 100 cuadros sin personas, en las cuatro variantes, con y sin niebla. El camión y las rocas no se confunden con personas.
- **Carga del modelo:** 44 s la primera vez, por la compilación de los shaders de WebGL; 4,7 s con la caché ya caliente. **En la demo hay que abrir la página un rato antes.**
- **Peso del JPEG en la escena sintética:** A 4,2 KB, B 2,8 KB, C 1,8 KB. Las texturas y las fotos reales van a subirlo: hay que recalibrar con la escena final.
- **Hallazgo técnico:** si el panel del navegador queda oculto, `requestAnimationFrame` se detiene. El simulador no puede depender de él para la lógica de seguridad: el bucle de 20 Hz de la máquina va con temporizadores, y rAF queda solo para dibujar. En la consola, estar oculta cuenta como soltar el pedal de hombre muerto.

**Cómo repetir la medición con las fotos:**
1. Copiar los PNG a `teleop/web/maquina/personas/`, que está fuera de git.
2. Levantar el servidor con `python teleop/spike/servir.py`.
3. Abrir `http://127.0.0.1:8090/spike/camara.html` y apretar «Medir».

## 4. Enlace de radio (`teleop/spike/enlace_spike.py`)

**Qué se simula:** un enlace full dúplex, con eventos discretos y de forma determinista.
- **Prioridades:** control > datos > video.
- **Video:** se parte en fragmentos de 1200 B y solo vale el último cuadro.
- **Comandos:** tienen un TTL de 300 ms y un comando vencido jamás se ejecuta.
- **Sobrecarga:** 60 B por mensaje, más 20 ms de latencia base.
- **Ritmos:** comandos a 10 Hz y estado a 10 Hz (5 Hz en modo degradado).

| Enlace | Escalón de video | Comandos entregados | Demora de comandos (p99) | Estado (p99) | Video | Uso de bajada |
|---|---|---|---|---|---|---|
| 1 Mbps | B | 100 % | 22 ms | 37 ms | 6 cuadros/s | 19 % (JPEG medido) · 48 % (JPEG conservador) |
| 256 kbps | C | 100 % | 28 ms | 47 ms | 4 cuadros/s | 42 % · 72 % |
| 64 kbps | D (solo mapa) | 100 % | 54 ms | 129 ms | — | 49 % |
| 64 kbps con 2 % de pérdida de radio | D | 97,6 % | 54 ms | 129 ms | — | 49 % |
| 32 kbps (por debajo del mínimo) | D | 100 % | 88 ms | 238 ms | — | 97 % (saturado) |

Con 2 % de pérdida, el mayor hueco entre comandos es de 200 ms: el vigilante de 500 ms no dispara. A 32 kbps la bajada se satura, así que 64 kbps queda confirmado como mínimo operativo. La prueba con WebSockets reales es parte de la estación, en la Fase 1.

## 5. Rendimiento en condiciones de demo: pendiente

**Con la app de Claude y Chrome abiertos y Docker Desktop apagado:**
- RAM libre: 0,98 GB.
- Consumo: la app de Claude unos 2 GB, Chrome unos 750 MB, y Edge con WebView2 unos 630 MB.

**Falta medir en la configuración real de la demo:**
1. Cerrar la app de Claude y Chrome.
2. Abrir Docker Desktop.
3. Levantar el stack liviano: `docker compose up -d db redpanda mqtt ingesta mantenimiento panel`.
4. Abrir el simulador y la consola en Edge.
5. Grabar con OBS usando el codificador de Intel.
6. Registrar `docker stats --no-stream`, la RAM libre y la fluidez.

Si no alcanza, el plan B es limitar la memoria de WSL con `.wslconfig` (decisión de Enrique), bajar la resolución del render y grabar por partes.
