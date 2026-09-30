// Reemplaza a descargar.py: consulta la API de accesibilidad de EMOVA y
// guarda el resultado en Supabase.
//
// Pensada para correr disparada por pg_cron cada 2 minutos (ver
// supabase/sql/cron_consultar.sql), en vez del cron de GitHub Actions, que
// en runners compartidos puede demorarse bien por encima del intervalo
// pedido.
// También puede invocarse a mano (o desde el dashboard al cargar) para
// forzar un refresh; tiene un debounce corto para no pegarle dos veces
// seguidas a la API externa si eso pasa.
//
// Usa las variables SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY, que Supabase
// inyecta automáticamente en toda Edge Function del proyecto (no hace falta
// configurarlas a mano como secrets).

const EMOVA_URL =
  "https://aplicacioneswp.metrovias.com.ar/APIAccesibilidad/Accesibilidad.svc/GetLineas";

const DEBOUNCE_MS = 20_000;

const SUPABASE_URL = Deno.env.get("SUPABASE_URL")!;
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders, "Content-Type": "application/json" },
  });
}

async function rest(
  path: string,
  init: RequestInit & { prefer?: string } = {},
) {
  const headers: Record<string, string> = {
    apikey: SERVICE_KEY,
    Authorization: `Bearer ${SERVICE_KEY}`,
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (init.prefer) headers["Prefer"] = init.prefer;

  const res = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    throw new Error(`Supabase ${res.status} en ${path}: ${await res.text()}`);
  }
  // Con `Prefer: return=minimal` PostgREST responde 201 con body vacío (no
  // 204), así que no alcanza con mirar el status.
  const text = await res.text();
  return text ? JSON.parse(text) : null;
}

const DATE_RE = /\/Date\((-?\d+)([+-]\d{4})?\)\//;

function parseApiDate(value?: string | null): string | null {
  if (!value) return null;
  const m = DATE_RE.exec(value);
  if (!m) return null;
  return new Date(Number(m[1])).toISOString();
}

interface Acceso {
  linea: string;
  estacion: string;
  nombre: string;
  descripcion_linea: string | null;
  descripcion: string | null;
  tipo: number | null;
  estado: number | null;
  funcionando: boolean | null;
  fecha_actualizacion_api: string | null;
}

function aplanar(data: any[]): Acceso[] {
  const accesos: Acceso[] = [];
  for (const linea of data ?? []) {
    const nombreLinea = linea.nombre;
    const descripcionLinea = linea.descripcion ?? null;
    for (const estacion of linea.estaciones ?? []) {
      const nombreEstacion = estacion.nombre;
      for (const acceso of estacion.accesos ?? []) {
        accesos.push({
          linea: nombreLinea,
          estacion: nombreEstacion,
          nombre: acceso.nombre,
          descripcion_linea: descripcionLinea,
          descripcion: acceso.descripcion ?? null,
          tipo: acceso.tipo ?? null,
          estado: acceso.estado ?? null,
          funcionando: acceso.funcionando ?? null,
          fecha_actualizacion_api: parseApiDate(acceso.fechaActualizacion),
        });
      }
    }
  }
  return accesos;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  try {
    const ahora = new Date().toISOString();

    const actuales = (await rest("estado_actual?select=*")) as any[];
    const ultimaConsultaPrevia = actuales.reduce(
      (max, r) => (r.ultima_consulta > max ? r.ultima_consulta : max),
      "",
    );
    if (
      ultimaConsultaPrevia &&
      Date.parse(ahora) - Date.parse(ultimaConsultaPrevia) < DEBOUNCE_MS
    ) {
      return jsonResponse({ skipped: true, motivo: "consulta reciente" });
    }

    const apiRes = await fetch(EMOVA_URL);
    if (!apiRes.ok) {
      throw new Error(`EMOVA API ${apiRes.status}`);
    }
    const data = await apiRes.json();
    const accesos = aplanar(data);

    if (accesos.length === 0) {
      return jsonResponse({ error: "Sin datos recibidos de la API" }, 502);
    }

    await rest("accesos?on_conflict=linea,estacion,nombre", {
      method: "POST",
      prefer: "resolution=merge-duplicates,return=minimal",
      body: JSON.stringify(
        accesos.map((a) => ({
          linea: a.linea,
          descripcion_linea: a.descripcion_linea,
          estacion: a.estacion,
          nombre: a.nombre,
          descripcion: a.descripcion,
          tipo: a.tipo,
        })),
      ),
    });

    const catalogo = (await rest(
      "accesos?select=id,linea,estacion,nombre",
    )) as any[];
    const idPorKey = new Map<string, number>();
    for (const r of catalogo) {
      idPorKey.set(`${r.linea}|${r.estacion}|${r.nombre}`, r.id);
    }

    const actualesPorId = new Map<number, any>();
    for (const r of actuales) actualesPorId.set(r.acceso_id, r);

    const cambios: any[] = [];
    const upserts: any[] = [];

    for (const acceso of accesos) {
      const key = `${acceso.linea}|${acceso.estacion}|${acceso.nombre}`;
      const accesoId = idPorKey.get(key);
      if (accesoId === undefined) {
        throw new Error(`No se encontró id para el acceso ${key}`);
      }
      const previo = actualesPorId.get(accesoId);

      const cambio =
        !previo ||
        previo.estado !== acceso.estado ||
        previo.funcionando !== acceso.funcionando;

      if (cambio) {
        cambios.push({
          acceso_id: accesoId,
          estado_anterior: previo ? previo.estado : null,
          estado_nuevo: acceso.estado,
          funcionando_anterior: previo ? previo.funcionando : null,
          funcionando_nuevo: acceso.funcionando,
          timestamp: ahora,
          fecha_actualizacion_api: acceso.fecha_actualizacion_api,
        });
      }

      upserts.push({
        acceso_id: accesoId,
        estado: acceso.estado,
        funcionando: acceso.funcionando,
        fecha_actualizacion_api: acceso.fecha_actualizacion_api,
        ultima_consulta: ahora,
        ultimo_cambio: cambio ? ahora : previo.ultimo_cambio,
      });
    }

    await rest("estado_actual?on_conflict=acceso_id", {
      method: "POST",
      prefer: "resolution=merge-duplicates,return=minimal",
      body: JSON.stringify(upserts),
    });

    if (cambios.length > 0) {
      await rest("estado_historial", {
        method: "POST",
        prefer: "return=minimal",
        body: JSON.stringify(cambios),
      });
    }

    return jsonResponse({
      ok: true,
      timestamp: ahora,
      accesos: accesos.length,
      cambios: cambios.length,
    });
  } catch (err) {
    console.error(err);
    return jsonResponse({ error: String(err) }, 500);
  }
});
