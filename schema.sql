-- Esquema para el proyecto de accesibilidad del subte (EMOVA)
-- Ejecutar en el SQL Editor de Supabase (Project > SQL Editor > New query)

-- Dimensión: catálogo de accesos físicos (ascensores/escaleras). No crece con
-- el tiempo (~424 filas fijas), así que el texto (línea/estación/descripción)
-- vive acá una sola vez y no se repite en cada lectura.
create table if not exists accesos (
    id smallint generated always as identity primary key,
    linea text not null,
    descripcion_linea text,
    estacion text not null,
    nombre text not null,                 -- código del acceso (ej: A1, E2, AS)
    descripcion text,
    tipo smallint,
    unique (linea, estacion, nombre)
);

-- Estado actual de cada acceso: una fila por acceso (tamaño fijo, no crece).
create table if not exists estado_actual (
    acceso_id smallint primary key references accesos (id),
    estado smallint,
    funcionando boolean,
    fecha_actualizacion_api timestamptz,
    ultima_consulta timestamptz not null,
    ultimo_cambio timestamptz not null
);

-- Historial de cambios: la única tabla que crece con el tiempo.
-- Solo guarda acceso_id (2 bytes) en vez de repetir linea/estacion/descripcion.
create table if not exists estado_historial (
    id bigint generated always as identity primary key,
    acceso_id smallint not null references accesos (id),
    estado_anterior smallint,
    estado_nuevo smallint,
    funcionando_anterior boolean,
    funcionando_nuevo boolean,
    "timestamp" timestamptz not null default now(),
    fecha_actualizacion_api timestamptz
);

create index if not exists idx_historial_acceso_ts
    on estado_historial (acceso_id, "timestamp" desc);

-- Lectura pública (para poder armar un dashboard/front sin backend propio),
-- escritura solo con la service_role key (la usa el workflow de GitHub Actions
-- y por diseño ignora RLS, así que no hace falta política de insert/update).
alter table accesos enable row level security;
alter table estado_actual enable row level security;
alter table estado_historial enable row level security;

create policy "lectura publica accesos"
    on accesos for select
    using (true);

create policy "lectura publica estado_actual"
    on estado_actual for select
    using (true);

create policy "lectura publica estado_historial"
    on estado_historial for select
    using (true);

-- Vistas de conveniencia con los nombres ya "des-normalizados" para consultar
-- desde un dashboard sin tener que hacer el join a mano cada vez.
create or replace view estado_actual_legible as
select
    a.linea,
    a.estacion,
    a.nombre,
    a.descripcion,
    e.estado,
    e.funcionando,
    e.fecha_actualizacion_api,
    e.ultima_consulta,
    e.ultimo_cambio
from estado_actual e
join accesos a on a.id = e.acceso_id;

create or replace view historial_legible as
select
    h.id,
    a.linea,
    a.estacion,
    a.nombre,
    a.descripcion,
    h.estado_anterior,
    h.estado_nuevo,
    h.funcionando_anterior,
    h.funcionando_nuevo,
    h."timestamp",
    h.fecha_actualizacion_api
from estado_historial h
join accesos a on a.id = h.acceso_id;
