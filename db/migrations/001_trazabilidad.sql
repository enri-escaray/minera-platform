-- 001_trazabilidad.sql — esquema base de cadena de custodia del mineral
-- Convención: solo migraciones forward. Correcciones = nueva migración.

BEGIN;

CREATE SCHEMA IF NOT EXISTS trazabilidad;
SET search_path TO trazabilidad;

CREATE TABLE bloque_geologico (
    id              text PRIMARY KEY,          -- id del modelo de bloques externo (Deswik/Vulcan)
    origen_modelo   text NOT NULL,             -- nombre/versión del modelo importado
    centroide       point NOT NULL,
    cota            numeric(8,2) NOT NULL,
    leyes_estimadas jsonb NOT NULL,            -- {"cu_pct": 0.85, "au_gpt": 0.12, ...}
    importado_en    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE disparo (
    id            uuid PRIMARY KEY,
    codigo        text UNIQUE NOT NULL,        -- ej: "DSP-2026-0712-N3"
    fecha         timestamptz NOT NULL,
    poligono      jsonb,                       -- geometría simplificada del polígono de voladura
    creado_en     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE disparo_bloque (
    disparo_id  uuid REFERENCES disparo(id),
    bloque_id   text REFERENCES bloque_geologico(id),
    fraccion    numeric(5,4) NOT NULL DEFAULT 1.0,  -- fracción del bloque afectada
    PRIMARY KEY (disparo_id, bloque_id)
);

CREATE TYPE tipo_lote AS ENUM ('rom', 'stock', 'alimentacion_planta', 'concentrado', 'relave');
CREATE TYPE estado_lote AS ENUM ('activo', 'consumido', 'embarcado', 'cerrado');

CREATE TABLE lote_mineral (
    id          uuid PRIMARY KEY,
    codigo      text UNIQUE NOT NULL,          -- ej: "LT-2026-018443"
    disparo_id  uuid REFERENCES disparo(id),   -- NULL para lotes producidos por planta
    tipo        tipo_lote NOT NULL,
    estado      estado_lote NOT NULL DEFAULT 'activo',
    creado_en   timestamptz NOT NULL DEFAULT now()
);

-- Grafo de custodia: qué lotes alimentaron a qué lote producido (mezclas en stockpile / planta)
CREATE TABLE lote_origen (
    lote_id        uuid REFERENCES lote_mineral(id),
    lote_origen_id uuid REFERENCES lote_mineral(id),
    fraccion_masa  numeric(6,5),               -- proporción estimada del origen en el producto
    PRIMARY KEY (lote_id, lote_origen_id),
    CHECK (lote_id <> lote_origen_id)
);

CREATE TYPE tipo_ubicacion AS ENUM ('frente', 'stockpile', 'tolva', 'planta', 'canchas', 'puerto');

CREATE TABLE ubicacion (
    id     text PRIMARY KEY,
    nombre text NOT NULL,
    tipo   tipo_ubicacion NOT NULL
);

CREATE TABLE movimiento (
    id            uuid PRIMARY KEY,
    lote_id       uuid NOT NULL REFERENCES lote_mineral(id),
    equipo_id     text NOT NULL,               -- id de flota (camión, cargador, correa)
    origen_id     text NOT NULL REFERENCES ubicacion(id),
    destino_id    text NOT NULL REFERENCES ubicacion(id),
    inicio        timestamptz NOT NULL,
    fin           timestamptz,
    creado_en     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX movimiento_lote_idx ON movimiento (lote_id, inicio);

CREATE TABLE pesaje (
    event_id       uuid PRIMARY KEY,           -- idempotencia: reintentos no duplican masa
    lote_id        uuid NOT NULL REFERENCES lote_mineral(id),
    movimiento_id  uuid REFERENCES movimiento(id),
    instrumento_id text NOT NULL,              -- báscula/pesómetro calibrado
    masa_kg        numeric(12,2) NOT NULL CHECK (masa_kg > 0),
    medido_en      timestamptz NOT NULL,
    ajusta_a       uuid REFERENCES pesaje(event_id),  -- NULL salvo registros de ajuste
    creado_en      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX pesaje_lote_idx ON pesaje (lote_id, medido_en);

CREATE TABLE analisis_laboratorio (
    id           uuid PRIMARY KEY,
    lote_id      uuid NOT NULL REFERENCES lote_mineral(id),
    muestra_cod  text NOT NULL,
    elemento     text NOT NULL,                -- 'cu', 'au', 'fe', 'as', ...
    ley          numeric(10,4) NOT NULL,
    unidad       text NOT NULL,                -- 'pct', 'gpt', 'ppm'
    metodo       text NOT NULL,
    laboratorio  text NOT NULL,
    medido_en    timestamptz NOT NULL,
    creado_en    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX analisis_lote_idx ON analisis_laboratorio (lote_id, elemento);

CREATE TABLE proceso_planta (
    id            uuid PRIMARY KEY,
    linea         text NOT NULL,
    inicio        timestamptz NOT NULL,
    fin           timestamptz,
    recuperacion  numeric(5,4),                -- calculada en conciliación
    creado_en     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proceso_lote (
    proceso_id uuid REFERENCES proceso_planta(id),
    lote_id    uuid REFERENCES lote_mineral(id),
    rol        text NOT NULL CHECK (rol IN ('entrada', 'salida')),
    PRIMARY KEY (proceso_id, lote_id, rol)
);

CREATE TABLE embarque (
    id          uuid PRIMARY KEY,
    codigo      text UNIQUE NOT NULL,
    destino     text NOT NULL,
    zarpe       timestamptz,
    documentos  jsonb NOT NULL DEFAULT '{}'::jsonb,
    creado_en   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE embarque_lote (
    embarque_id uuid REFERENCES embarque(id),
    lote_id     uuid REFERENCES lote_mineral(id),
    PRIMARY KEY (embarque_id, lote_id)
);

-- Auditoría inmutable transversal (append-only; sin UPDATE/DELETE por permisos)
CREATE TABLE auditoria (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor     text NOT NULL,                   -- usuario o servicio
    accion    text NOT NULL,                   -- 'lote.creado', 'pesaje.registrado', ...
    entidad   text NOT NULL,
    entidad_id text NOT NULL,
    payload   jsonb NOT NULL,
    origen_ip inet,
    ts        timestamptz NOT NULL DEFAULT now()
);
REVOKE UPDATE, DELETE ON auditoria FROM PUBLIC;

COMMIT;

-- Telemetría (requiere extensión timescaledb; separada de la transacción anterior)
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE TABLE IF NOT EXISTS trazabilidad.telemetria_equipo (
    ts        timestamptz NOT NULL,
    equipo_id text NOT NULL,
    metrica   text NOT NULL,                   -- 'gps_lat','gps_lon','carga_ton','vibracion_rms'
    valor     double precision NOT NULL
);
SELECT create_hypertable('trazabilidad.telemetria_equipo', 'ts', if_not_exists => TRUE);
SELECT add_retention_policy('trazabilidad.telemetria_equipo', INTERVAL '90 days', if_not_exists => TRUE);
