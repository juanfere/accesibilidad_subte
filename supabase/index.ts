// Reemplaza a descargar.py: consulta la API de accesibilidad de EMOVA y
// guarda el resultado en Supabase.
//
// Pensada para correr disparada por pg_cron cada 5 minutos (ver
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
  "https://apiaccesibilidad.emova.com.ar/Accesibilidad.svc/getlineas";

const DEBOUNCE_MS = 20_000;

// Vigilancia externa (Healthchecks.io). Opcional: si el secret
// HEALTHCHECK_URL no está definido, no se avisa a nadie y todo funciona igual.
// Es la URL de ping del chequeo, p. ej. https://hc-ping.com/<uuid>
const HEALTHCHECK_URL = (Deno.env.get("HEALTHCHECK_URL") ?? "").replace(/\/+$/, "");

// Minutos máximos que puede tener el dato "en vivo" (modo 1) de la API antes
// de considerarlo viejo. Si se pasa (y alguna línea está en horario de
// servicio), la lectura se registra igual pero NO se avisa "estoy viva" a
// Healthchecks, así que salta la alerta. 0 = no controlar.
const FRESCURA_MAX_MIN = Number(Deno.env.get("FRESCURA_MAX_MIN") ?? "20");

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

// Avisa a Healthchecks.io. "" = lectura exitosa ("estoy viva"); "/log" =
// anota un mensaje sin cambiar el estado del chequeo (así un fallo aislado
// no dispara alerta: el alerta salta cuando dejan de llegar lecturas
// exitosas). Un fallo de este aviso NUNCA debe romper la función.
async function avisar(sufijo: "" | "/log", mensaje: string) {
  if (!HEALTHCHECK_URL) return;
  try {
    await fetch(HEALTHCHECK_URL + sufijo, {
      method: "POST",
      body: mensaje.slice(0, 1000),
      signal: AbortSignal.timeout(5_000),
    });
  } catch (e) {
    console.error("No se pudo avisar a Healthchecks:", e);
  }
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
  // La API trae dos tipos de dato: modo 1 = "en vivo" (todos comparten una
  // misma fecha de lectura) y modo 0 = "por lote" (una fecha por línea, que
  // se renueva cada tanto). Observado en los datos; EMOVA no lo documenta.
  modo: number | null;
  automatizado: boolean | null;
  fuera_de_horario: boolean | null; // de la línea
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
          modo: acceso.modo ?? null,
          automatizado: acceso.automatizado ?? null,
          fuera_de_horario: linea.fueraDeHorario ?? null,
        });
      }
    }
  }
  return accesos;
}

// Resume de dónde viene lo que devolvió la API: cuántos equipos son "en vivo"
// y cuántos "por lote", la fecha del dato en vivo y la del último lote de
// cada línea. Se guarda en `consultas` para poder revisar la fuente después.
function resumirFuente(accesos: Acceso[]) {
  let enVivo = 0;
  let porLote = 0;
  let fechaEnVivo: string | null = null;
  const lineas: Record<string, {
    en_vivo: number;
    por_lote: number;
    lote: string | null;
    fuera_de_horario: boolean | null;
  }> = {};
  for (const a of accesos) {
    const l = (lineas[a.linea] ??= {
      en_vivo: 0,
      por_lote: 0,
      lote: null,
      fuera_de_horario: a.fuera_de_horario,
    });
    const f = a.fecha_actualizacion_api;
    if (a.modo === 1) {
      enVivo++;
      l.en_vivo++;
      if (f && (!fechaEnVivo || f > fechaEnVivo)) fechaEnVivo = f;
    } else if (a.modo === 0) {
      porLote++;
      l.por_lote++;
      if (f && (!l.lote || f > l.lote)) l.lote = f;
    }
  }
  return { enVivo, porLote, fechaEnVivo, lineas };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  const ahora = new Date().toISOString();

  try {
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
      throw new Error("Sin datos recibidos de la API");
    }

   const clave = (a: { linea: string; estacion: string; nombre: string }) =>
      `${a.linea}|${a.estacion}|${a.nombre}`;

    // 1) Leer lo que ya está guardado (lectura: no gasta números del contador)
    let catalogo = (await rest(
      "accesos?select=id,linea,estacion,nombre,descripcion_linea,descripcion,tipo",
    )) as any[];
    const porClave = new Map(catalogo.map((r) => [clave(r), r]));

    // 2) Quedarse solo con lo que es nuevo o cambió
    const pendientes = accesos.filter((a) => {
      const c = porClave.get(clave(a));
      return (
        !c ||
        c.descripcion !== a.descripcion ||
        String(c.tipo ?? "") !== String(a.tipo ?? "") ||
        c.descripcion_linea !== a.descripcion_linea
      );
    });

    // 3) Escribir solo si hay algo pendiente (casi siempre no hay nada)
    if (pendientes.length > 0) {
      await rest("accesos?on_conflict=linea,estacion,nombre", {
        method: "POST",
        prefer: "resolution=merge-duplicates,return=minimal",
        body: JSON.stringify(
          pendientes.map((a) => ({
            linea: a.linea,
            descripcion_linea: a.descripcion_linea,
            estacion: a.estacion,
            nombre: a.nombre,
            descripcion: a.descripcion,
            tipo: a.tipo,
          })),
        ),
      });
      // Volver a leer para tener los ids de las filas recién creadas
      catalogo = (await rest(
        "accesos?select=id,linea,estacion,nombre",
      )) as any[];
    }

    const idPorKey = new Map<string, number>();
    for (const r of catalogo) {
      idPorKey.set(clave(r), r.id);
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
          modo_anterior: previo ? previo.modo ?? null : null,
          modo_nuevo: acceso.modo,
          timestamp: ahora,
          fecha_actualizacion_api: acceso.fecha_actualizacion_api,
          ultima_lectura_previa: previo ? previo.ultima_consulta : null,
        });
      }

      upserts.push({
        acceso_id: accesoId,
        estado: acceso.estado,
        funcionando: acceso.funcionando,
        modo: acceso.modo,
        fecha_actualizacion_api: acceso.fecha_actualizacion_api,
        ultima_consulta: ahora,
        ultimo_cambio: cambio ? ahora : previo.ultimo_cambio,
        lecturas_en_estado: cambio ? 1 : previo.lecturas_en_estado + 1,
        // El cambio ocurrió entre la lectura anterior y esta.
        cambio_desde: cambio
          ? (previo ? previo.ultima_consulta : null)
          : previo.cambio_desde,
        primera_consulta: previo ? previo.primera_consulta : ahora,
        ultima_vez_funcionando: acceso.funcionando
          ? ahora
          : (previo ? previo.ultima_vez_funcionando : null),
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

    const fuente = resumirFuente(accesos);

    await rest("consultas", {
      method: "POST",
      prefer: "return=minimal",
      body: JSON.stringify({
        timestamp: ahora,
        ok: true,
        accesos: accesos.length,
        cambios: cambios.length,
        funcionando: accesos.filter((a) => a.funcionando).length,
        en_vivo: fuente.enVivo,
        por_lote: fuente.porLote,
        fecha_en_vivo: fuente.fechaEnVivo,
        fuente: fuente.lineas,
      }),
    });

    // ¿El dato "en vivo" está al día? Solo se juzga si alguna línea está en
    // horario de servicio; fuera de horario puede no actualizarse.
    const enHorario = Object.values(fuente.lineas).some(
      (l) => l.fuera_de_horario === false,
    );
    const edadEnVivoMin = fuente.fechaEnVivo
      ? Math.round((Date.now() - Date.parse(fuente.fechaEnVivo)) / 60000)
      : null;
    const datosViejos = FRESCURA_MAX_MIN > 0 && enHorario &&
      (edadEnVivoMin === null || edadEnVivoMin > FRESCURA_MAX_MIN);

    if (datosViejos) {
      // Se anota el motivo pero NO se avisa "estoy viva": al no llegar el
      // aviso, Healthchecks termina alertando.
      await avisar(
        "/log",
        edadEnVivoMin === null
          ? "DATOS EN VIVO AUSENTES: la API no trae equipos con modo 1"
          : `DATOS EN VIVO VIEJOS: la última fecha en vivo es de hace ${edadEnVivoMin} min`,
      );
    } else {
      await avisar(
        "",
        `ok: ${accesos.length} accesos, ${cambios.length} cambios, ` +
          `${accesos.filter((a) => a.funcionando).length} funcionando, ` +
          `${fuente.enVivo} en vivo / ${fuente.porLote} por lote`,
      );
    }

    return jsonResponse({
      ok: true,
      timestamp: ahora,
      accesos: accesos.length,
      cambios: cambios.length,
      en_vivo: fuente.enVivo,
      por_lote: fuente.porLote,
      edad_en_vivo_min: edadEnVivoMin,
    });
  } catch (err) {
    console.error(err);
    // Queda registrado que se intentó y falló, para no confundir este
    // hueco con "no cambió nada".
    try {
      await rest("consultas", {
        method: "POST",
        prefer: "return=minimal",
        body: JSON.stringify({
          timestamp: ahora,
          ok: false,
          error: String(err).slice(0, 500),
        }),
      });
    } catch (logErr) {
      console.error(logErr);
    }
    await avisar("/log", `FALLÓ: ${String(err)}`);
    return jsonResponse({ error: String(err) }, 500);
  }
});
