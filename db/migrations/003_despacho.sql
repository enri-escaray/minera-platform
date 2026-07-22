-- 003_despacho.sql — persistencia del estado del rajo (despacho)
BEGIN;
CREATE SCHEMA IF NOT EXISTS despacho;

CREATE TABLE despacho.pala (
    id               text PRIMARY KEY,
    ubicacion_id     text NOT NULL,
    tiempo_carga_min numeric(6,2) NOT NULL CHECK (tiempo_carga_min > 0),
    prioridad_mezcla int NOT NULL DEFAULT 0,
    activa           boolean NOT NULL DEFAULT true
);

CREATE TABLE despacho.camion (
    id            text PRIMARY KEY,
    capacidad_ton numeric(8,2) NOT NULL CHECK (capacidad_ton > 0),
    estado        text NOT NULL DEFAULT 'disponible'
        CHECK (estado IN ('disponible','en_ciclo','mantenimiento'))
);

CREATE TABLE despacho.asignacion (
    id                  uuid PRIMARY KEY,
    camion_id           text NOT NULL REFERENCES despacho.camion(id),
    pala_id             text NOT NULL REFERENCES despacho.pala(id),
    espera_estimada_min numeric(8,2) NOT NULL,
    motivo              text NOT NULL,
    creada_en           timestamptz NOT NULL DEFAULT now(),
    liberada_en         timestamptz
);
-- Un camión no puede tener dos asignaciones vivas: misma técnica que el
-- antirrebote de mantenimiento, la invariante vive también en la base.
CREATE UNIQUE INDEX asignacion_viva_unica
    ON despacho.asignacion (camion_id) WHERE liberada_en IS NULL;
CREATE INDEX asignacion_pala_viva_idx
    ON despacho.asignacion (pala_id) WHERE liberada_en IS NULL;
COMMIT;
