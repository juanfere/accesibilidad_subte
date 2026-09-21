# Accesibilidad subte (EMOVA)

Junta periódicamente el estado de ascensores y escaleras mecánicas del subte
(API pública de EMOVA/Metrovías) y lo guarda en Supabase, para no depender de
tener una compu prendida corriendo un loop.

## Modelo de datos

En vez de guardar un snapshot completo en cada consulta (crecería muy rápido:
~424 accesos x cada 5 min = pasaría los 500MB gratis de Supabase en días),
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

### 4. Mover esto a un repo propio en GitHub

Este proyecto se armó dentro del repo `sandbox` (privado, mezclado con otras
cosas) para no ensuciar minutos de Actions de un repo privado con un cron
cada 5 min. Antes de activar el workflow:

1. Crear un repo nuevo en GitHub, **público** (así los minutos de Actions
   son gratis sin límite; los datos son públicos igual).
2. Copiar el contenido de esta carpeta (menos `accesibilidad_emova.csv` si
   ya la migraste) a ese repo. Importante: `.github/workflows/consultar.yml`
   tiene que quedar en la **raíz** del repo nuevo, GitHub Actions solo lee
   workflows ahí.
3. En el repo nuevo: **Settings → Secrets and variables → Actions → New
   repository secret**, cargar `SUPABASE_URL` y `SUPABASE_KEY`.
4. Push. El workflow corre cada 5 minutos automáticamente (`schedule` en
   `consultar.yml`); también se puede disparar a mano desde la pestaña
   **Actions → Consultar accesibilidad subte → Run workflow**
   (`workflow_dispatch`).

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
