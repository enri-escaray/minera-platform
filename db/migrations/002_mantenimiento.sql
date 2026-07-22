-- 002_mantenimiento.sql — persistencia de órdenes de trabajo (flujo P1)
BEGIN;
CREATE SCHEMA IF NOT EXISTS mantenimiento;

CREATE TABLE mantenimiento.orden_trabajo (
    id            uuid PRIMARY KEY,
    equipo_id     text NOT NULL,
    regla_id      text NOT NULL,
    origen        text NOT NULL DEFAULT 'predictivo',
    prioridad     text NOT NULL CHECK (prioridad IN ('critica','alta','media','baja')),
    estado        text NOT NULL DEFAULT 'abierta' CHECK (estado IN ('abierta','agendada','cerrada')),
    diagnostico   text NOT NULL,
    repuestos     jsonb NOT NULL DEFAULT '[]'::jsonb,
    valor_disparo numeric NOT NULL,
    creada_en     timestamptz NOT NULL DEFAULT now(),
    cerrada_en    timestamptz,
    cerrada_por   text,
    resolucion    text
);
-- Antirrebote a nivel de datos: una sola OT viva por (equipo, regla).
-- Es la misma invariante del evaluador, pero garantizada por la base:
-- sobrevive reinicios y réplicas concurrentes del servicio.
CREATE UNIQUE INDEX ot_viva_unica
    ON mantenimiento.orden_trabajo (equipo_id, regla_id)
    WHERE estado <> 'cerrada';
CREATE INDEX ot_estado_idx ON mantenimiento.orden_trabajo (estado, creada_en DESC);
COMMIT;
