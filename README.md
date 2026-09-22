# Accesibilidad subte (EMOVA)

Junta periódicamente el estado de ascensores y escaleras mecánicas del subte
(API pública de EMOVA/Metrovías) y lo guarda en Supabase, para no depender de
tener una compu prendida corriendo un loop.

## Modelo de datos

En vez de guardar un snapshot completo en cada consulta (crecería muy rápido:
~424 accesos x cada 2 min = pasaría los 500MB gratis de Supabase en días),
se guarda:

- `accesos`: dimensión con el catálogo de accesos físicos (línea, estación,
  descripción). Tamaño fijo (~424 filas), no crece con el tiempo.
- `estado_actual`: una fila por acceso con su estado más reciente (se
  actualiza siempre, tamaño fijo).
- `estado_historial`: la única tabla que crece. Una fila nueva solo cuando
  el estado de un acceso cambió respecto de la consulta anterior, y
  referenciando el acceso por `acceso_id` (smallint) en vez de repetir el
  texto de línea/estación/descripción en cada fila.

Ver `schema.sql`. Hay vistas (`estado_actual_legible`, `historial_legible`)
que ya traen el join hecho, para consultar sin tener que armarlo a mano.

### Cuánto dura el free tier de Supabase (500MB)

Con la tasa de cambios real medida en `accesibilidad_emova.csv` (518 cambios
en 2.95hs ≈ 175/hora en toda la red — ojo, ~56% de eso lo generan un puñado
de escaleras que "flapean" cada pocos minutos, puede no ser representativo
a largo plazo) y el esquema normalizado (~150-200 bytes por fila de
historial con índices), la estimación es:

- ~2.6 a 3.5 millones de filas de historial caben en 500MB.
- A ese ritmo, seria ~20 meses antes de llenar el free tier.

Si en algún momento se acerca al límite, la opción más simple es exportar
periódicamente las filas viejas de `estado_historial` a un archivo (CSV/
parquet) y borrarlas de la tabla (hot/cold storage), o pasar a Supabase Pro.

## Setup

### 1. Crear proyecto en Supabase

1. Entrar a https://supabase.com, crear cuenta/loguearse, "New project"
   (plan Free).
2. Una vez creado, ir a **SQL Editor** → **New query**, pegar el contenido
   de `schema.sql` y ejecutarlo.
3. Ir a **Project Settings → API** y copiar:
   - `Project URL` → `SUPABASE_URL`
   - `service_role` key (¡no la `anon`!) → `SUPABASE_KEY`. Esta key
     bypassea RLS y es la que necesita el script para escribir. **No la
     expongas en frontend**, solo en el secret de GitHub Actions / tu `.env`
     local.

### 2. Probar en local

```bash
cp .env.example .env   # completar con los valores reales
pip install -r requirements.txt
export $(cat .env | xargs)   # o usar direnv/python-dotenv
python descargar.py
```

Debería imprimir algo como:
`[...] OK - 424 accesos consultados, 424 cambios registrados` (todos
"cambian" la primera vez porque no había estado previo).

### 3. (Opcional) Migrar el historial que ya juntaste en CSV

```bash
python migrar_csv.py
```

### 4. Programar la consulta periódica (Edge Function + pg_cron)

La consulta cada 2 min **no** corre más como cron de GitHub Actions: en
runners compartidos el evento `schedule` puede demorarse bien por encima
del intervalo pedido, o directamente saltearse ejecuciones (le pasaba a
este proyecto). En su lugar, la misma lógica de `descargar.py` vive como
una Edge Function de Supabase (`supabase/functions/consultar`), programada
con `pg_cron` desde **adentro** de la base — un scheduler real, sin cola de
CI compartida de por medio. El workflow de GitHub (`.github/workflows/consultar.yml`)
quedó solo para correr `descargar.py` a mano si hace falta debuggear.

1. **Deployar la función.** Sin necesidad de la Supabase CLI: en el
   dashboard, ir a **Edge Functions → Create a new function**, nombrarla
   `consultar`, y pegar el contenido de `supabase/functions/consultar/index.ts`.
   Deployar. No hace falta configurar ningún secret: `SUPABASE_URL` y
   `SUPABASE_SERVICE_ROLE_KEY` ya están disponibles automáticamente dentro
   de toda Edge Function del proyecto.
2. **Probarla a mano** antes de programarla: en la misma pantalla de la
   función hay un botón para invocarla (o `curl -X POST
   https://xxxxxxxxxxxx.supabase.co/functions/v1/consultar -H "Authorization:
   Bearer <anon key>" -H "apikey: <anon key>"`). Debería devolver algo como
   `{"ok":true,"accesos":424,"cambios":...}`.
3. **Programarla con pg_cron.** Ir a **SQL Editor → New query**, pegar el
   contenido de `supabase/sql/cron_consultar.sql`, reemplazar los dos
   placeholders (`SUPABASE_URL` y la `anon` key — los mismos valores que ya
   están hardcodeados en `docs/index.html`) y ejecutar. Corre una sola vez;
   a partir de ahí `pg_cron` invoca la función cada 2 minutos solo.
4. **Verificar que quedó corriendo**: `select * from cron.job;` para ver el
   job programado, o `select * from cron.job_run_details order by
   start_time desc limit 20;` para ver las últimas ejecuciones.

El dashboard (`docs/index.html`) además llama a esta misma función al
cargar, para forzar un refresh apenas alguien entra a mirar (con timeout
corto y sin bloquear si falla); la función tiene un debounce de 20s para no
pegarle dos veces seguidas a la API de EMOVA si eso coincide con el paso de
`pg_cron`.

### 5. Mover esto a un repo propio en GitHub

Este proyecto se armó dentro del repo `sandbox` (privado, mezclado con otras
cosas). Antes de usarlo en serio:

1. Crear un repo nuevo en GitHub, público o privado (ya no depende de
   minutos de Actions gratis, así que no es obligatorio que sea público).
2. Copiar el contenido de esta carpeta (menos `accesibilidad_emova.csv` si
   ya la migraste) a ese repo.
3. Si vas a usar el workflow manual de debug: **Settings → Secrets and
   variables → Actions → New repository secret**, cargar `SUPABASE_URL` y
   `SUPABASE_KEY`.
4. Publicar `docs/` como GitHub Pages (**Settings → Pages → Source:
   Deploy from a branch → /docs**) si querés servir el dashboard desde ahí.

## Consultar los datos

Con la `anon` key (esa sí es pública, las tablas tienen RLS de solo lectura)
se puede consultar directo desde cualquier lado vía la API REST automática
de Supabase, por ejemplo:

```
GET https://xxxxxxxxxxxx.supabase.co/rest/v1/estado_actual?select=*
apikey: <anon key>
```

o con `estado_historial?linea=eq.Línea A&order=timestamp.desc` para ver
cambios recientes de una línea. Esto es lo que usaría un dashboard después
sin necesitar backend propio.
