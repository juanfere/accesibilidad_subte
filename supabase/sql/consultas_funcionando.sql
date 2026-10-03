-- Agrega a `consultas` cuántos accesos funcionaban en cada lectura, para
-- poder graficar el % funcionando lectura por lectura (pestaña Histórico
-- del dashboard).
--
-- Correr una sola vez en el SQL Editor de Supabase. Es idempotente: se
-- puede volver a correr sin problema.

alter table consultas add column if not exists funcionando smallint;

-- Backfill de las lecturas anteriores a esta columna: reconstruye el estado
-- de cada acceso en el momento de la lectura a partir de estado_historial
-- (el último cambio registrado hasta ese instante).
update consultas c
set funcionando = (
    select count(*)
    from (
        select distinct on (h.acceso_id) h.funcionando_nuevo
        from estado_historial h
        where h."timestamp" <= c."timestamp"
        order by h.acceso_id, h."timestamp" desc
    ) ultimo
    where ultimo.funcionando_nuevo
)
where c.ok and c.funcionando is null;
