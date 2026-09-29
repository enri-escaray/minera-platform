# Librerías de terceros incluidas en teleop/web/vendor/

Se descargan con `python tools/vendorizar_teleop.py` y se verifican con SHA-256
(ver `vendor.lock.json`). Van copiadas en el repo para que la demo funcione sin internet.

| Componente | Versión | Licencia | Origen | Archivos |
|---|---|---|---|---|
| three.js | 0.186.1 | MIT (`three/LICENSE`) | npm `three` | `three/three.module.js`, `three/three.core.js`, `three/addons/OrbitControls.js` |
| TensorFlow.js | 4.22.0 | Apache-2.0 (`tfjs/LICENSE`) | npm `@tensorflow/tfjs` | `tfjs/tf.min.js` |
| COCO-SSD | 2.2.3 | Apache-2.0 (`coco-ssd/LICENSE`) | npm `@tensorflow-models/coco-ssd` | `coco-ssd/coco-ssd.min.js` |
| Modelo SSDLite MobileNet v2 (COCO) | publicado por TensorFlow.js | Apache-2.0 (proyecto tfjs-models) | storage.googleapis.com/tfjs-models | `modelos/ssdlite_mobilenet_v2/*` |

Se declaran también en la entrega del hackatón (declaración de IA y de componentes de terceros).
