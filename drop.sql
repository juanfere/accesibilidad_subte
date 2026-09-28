-- ⚠️ BORRA TODO: tablas, vistas, políticas y TODOS los datos juntados
-- (incluido el historial de cambios). No se puede deshacer.
--
-- Solo para empezar de cero: ejecutar este archivo y después schema.sql,
-- en el SQL Editor de Supabase (Project > SQL Editor > New query).

drop view if exists estado_actual_legible, historial_legible;

-- cascade también elimina índices, políticas RLS y claves foráneas.
drop table if exists estado_historial, estado_actual, consultas, accesos cascade;
