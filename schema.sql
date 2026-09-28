-- Esquema para el proyecto de accesibilidad del subte (EMOVA)
-- Ejecutar en el SQL Editor de Supabase (Project > SQL Editor > New query)
-- Para empezar de cero sobre una base existente, correr antes drop.sql
-- (borra todos los datos).

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
    ultimo_cambio timestamptz not null,
    -- Lecturas exitosas consecutivas en el estado actual (incluida la que
    -- detectó el cambio).
    lecturas_en_estado integer not null default 1,
    -- Última lectura exitosa ANTERIOR al cambio: el cambio ocurrió entre
    -- cambio_desde y ultimo_cambio. NULL si está así desde la primera lectura.
    cambio_desde timestamptz,
    -- Primera vez que se leyó este acceso.
    primera_consulta timestamptz not null,
    -- Última lectura en la que se lo vio funcionando. NULL = nunca, desde
    -- que medimos. Permite ver lo que no anduvo en todo el día o nunca.
    ultima_vez_funcionando timestamptz
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
    fecha_actualizacion_api timestamptz,
    ultima_lectura_previa timestamptz      -- lectura anterior al cambio
);

create index if not exists idx_historial_acceso_ts
    on estado_historial (acceso_id, "timestamp" desc);

-- Una fila por corrida del recolector, haya salido bien o no. Permite
-- distinguir "no cambió" (hubo lectura ok sin cambio) de "no hubo lectura".
create table if not exists consultas (
    id bigint generated always as identity primary key,
    "timestamp" timestamptz not null default now(),
    ok boolean not null,
    accesos smallint,                     -- accesos devueltos por la API
    cambios smallint,                     -- filas agregadas a estado_historial
    error text                            -- motivo, si ok = false
);

create index if not exists idx_consultas_ts on consultas ("timestamp" desc);

-- Lectura pública (para poder armar un dashboard/front sin backend propio),
-- escritura solo con la service_role key (la usa el workflow de GitHub Actions
-- y por diseño ignora RLS, así que no hace falta política de insert/update).
alter table accesos enable row level security;
alter table estado_actual enable row level security;
alter table estado_historial enable row level security;
alter table consultas enable row level security;

create policy "lectura publica accesos"
    on accesos for select
    using (true);

create policy "lectura publica estado_actual"
    on estado_actual for select
    using (true);

create policy "lectura publica estado_historial"
    on estado_historial for select
    using (true);

create policy "lectura publica consultas"
    on consultas for select
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
    e.ultimo_cambio,
    e.lecturas_en_estado,
    e.cambio_desde,
    e.primera_consulta,
    e.ultima_vez_funcionando
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
    h.fecha_actualizacion_api,
    h.ultima_lectura_previa
from estado_historial h
join accesos a on a.id = h.acceso_id;
