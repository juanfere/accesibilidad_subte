-- Programa la Edge Function `consultar` (supabase/functions/consultar) para
-- correr cada 5 minutos usando pg_cron + pg_net, en vez de depender del
-- cron de GitHub Actions (poco confiable en runners compartidos: los
-- eventos `schedule` pueden demorarse bien por encima del intervalo
-- pedido, o directamente saltearse ejecuciones).
--
-- A 5': ~8.640 invocaciones/mes (500.000 incluidas en el free tier de
-- Supabase) y bien por debajo del egress incluido (5GB/mes) — el límite
-- real, si existe, está del lado de la API de EMOVA (no nuestra), no de
-- Supabase.
--
-- Correr una sola vez en el SQL Editor de Supabase (Project > SQL Editor >
-- New query), DESPUÉS de haber deployado la función. Si ya la habías
-- corrido antes con otro intervalo, volver a correr este script pisa el
-- job existente (mismo job name) sin necesidad de unschedule manual.
--
-- Reemplazar los dos placeholders de más abajo por los mismos valores que
-- ya están hardcodeados en docs/index.html (SUPABASE_URL y
-- SUPABASE_ANON_KEY) — la anon key alcanza porque la función solo exige un
-- JWT válido del proyecto, la escritura la hace ella misma puertas adentro
-- con la service_role key.

create extension if not exists pg_cron with schema extensions;
create extension if not exists pg_net with schema extensions;

select cron.schedule(
  'consultar-accesibilidad-subte',
  '*/5 * * * *',
  $$
  select net.http_post(
    url := 'https://xxxxxxxxxxxx.supabase.co/functions/v1/consultar',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer TU_ANON_KEY_ACA'
    )
  );
  $$
);

-- Para verificar que el job quedó programado:
--   select * from cron.job;
--
-- Para ver el historial de ejecuciones (útil para debug):
--   select * from cron.job_run_details order by start_time desc limit 20;
--
-- Para desprogramarlo si hace falta:
--   select cron.unschedule('consultar-accesibilidad-subte');
