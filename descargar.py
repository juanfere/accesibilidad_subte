"""Consulta la API de accesibilidad de EMOVA y guarda el resultado en Supabase.

Pensado para correr como una única ejecución (por ejemplo disparado por un
cron de GitHub Actions cada pocos minutos), no como loop infinito: guarda el
estado actual de cada acceso y solo agrega una fila al historial cuando algo
cambió respecto de la consulta anterior. El texto (línea/estación/descripción)
vive en la tabla `accesos` y el historial solo referencia su id, para no
repetirlo en cada fila (ver schema.sql).

Cada corrida, salga bien o no, deja una fila en `consultas`, y cada acceso
lleva la cuenta de cuántas lecturas seguidas lo vieron en su estado actual:
así se puede distinguir "no cambió" de "no hubo lectura".

Variables de entorno requeridas:
    SUPABASE_URL
    SUPABASE_KEY   (service_role key: bypassea RLS para poder escribir)
"""

import os
import re
import sys
from datetime import datetime, timezone

import requests
from supabase import create_client

URL = "https://aplicacioneswp.metrovias.com.ar/APIAccesibilidad/Accesibilidad.svc/GetLineas"

_DATE_RE = re.compile(r"/Date\((-?\d+)([+-]\d{4})?\)/")


def parse_api_date(value):
    """Convierte el formato /Date(1790015608093-0300)/ a ISO 8601."""
    if not value:
        return None
    m = _DATE_RE.match(value)
    if not m:
        return None
    millis = int(m.group(1))
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat()


def consultar():
    response = requests.get(URL, timeout=30)
    response.raise_for_status()
    return response.json()


def aplanar(data):
    accesos = []
    for linea in data:
        nombre_linea = linea.get("nombre")
        descripcion_linea = linea.get("descripcion")

        for estacion in linea.get("estaciones", []):
            nombre_estacion = estacion.get("nombre")

            for acceso in estacion.get("accesos", []):
                accesos.append({
                    "linea": nombre_linea,
                    "estacion": nombre_estacion,
                    "nombre": acceso.get("nombre"),
                    "descripcion_linea": descripcion_linea,
                    "descripcion": acceso.get("descripcion"),
                    "tipo": acceso.get("tipo"),
                    "estado": acceso.get("estado"),
                    "funcionando": acceso.get("funcionando"),
                    "fecha_actualizacion_api": parse_api_date(acceso.get("fechaActualizacion")),
                })
    return accesos


def sincronizar_dimension(supabase, accesos):
    """Upsertea el catálogo de accesos y devuelve {(linea,estacion,nombre): id}."""
    dimension_rows = [{
        "linea": a["linea"],
        "descripcion_linea": a["descripcion_linea"],
        "estacion": a["estacion"],
        "nombre": a["nombre"],
        "descripcion": a["descripcion"],
        "tipo": a["tipo"],
    } for a in accesos]

    supabase.table("accesos").upsert(dimension_rows, on_conflict="linea,estacion,nombre").execute()

    catalogo = supabase.table("accesos").select("id,linea,estacion,nombre").execute().data
    return {(r["linea"], r["estacion"], r["nombre"]): r["id"] for r in catalogo}


def registrar(supabase, ahora):
    data = consultar()
    accesos = aplanar(data)

    if not accesos:
        raise RuntimeError("Sin datos recibidos de la API")

    id_por_key = sincronizar_dimension(supabase, accesos)

    actuales = supabase.table("estado_actual").select("*").execute().data
    actuales_por_id = {r["acceso_id"]: r for r in actuales}

    cambios = []
    upserts = []

    for acceso in accesos:
        key = (acceso["linea"], acceso["estacion"], acceso["nombre"])
        acceso_id = id_por_key[key]
        previo = actuales_por_id.get(acceso_id)

        cambio = (
            previo is None
            or previo["estado"] != acceso["estado"]
            or previo["funcionando"] != acceso["funcionando"]
        )

        if cambio:
            cambios.append({
                "acceso_id": acceso_id,
                "estado_anterior": previo["estado"] if previo else None,
                "estado_nuevo": acceso["estado"],
                "funcionando_anterior": previo["funcionando"] if previo else None,
                "funcionando_nuevo": acceso["funcionando"],
                "timestamp": ahora,
                "fecha_actualizacion_api": acceso["fecha_actualizacion_api"],
                "ultima_lectura_previa": previo["ultima_consulta"] if previo else None,
            })

        upserts.append({
            "acceso_id": acceso_id,
            "estado": acceso["estado"],
            "funcionando": acceso["funcionando"],
            "fecha_actualizacion_api": acceso["fecha_actualizacion_api"],
            "ultima_consulta": ahora,
            "ultimo_cambio": ahora if cambio else previo["ultimo_cambio"],
            "lecturas_en_estado": 1 if cambio else previo["lecturas_en_estado"] + 1,
            # El cambio ocurrió entre la lectura anterior y esta.
            "cambio_desde": (previo["ultima_consulta"] if previo else None) if cambio else previo["cambio_desde"],
            "primera_consulta": previo["primera_consulta"] if previo else ahora,
            "ultima_vez_funcionando": ahora if acceso["funcionando"] else (previo["ultima_vez_funcionando"] if previo else None),
        })

    supabase.table("estado_actual").upsert(upserts, on_conflict="acceso_id").execute()

    if cambios:
        supabase.table("estado_historial").insert(cambios).execute()

    n_funcionando = sum(1 for a in accesos if a["funcionando"])
    return len(accesos), len(cambios), n_funcionando


def main():
    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    supabase = create_client(supabase_url, supabase_key)

    ahora = datetime.now(timezone.utc).isoformat()

    try:
        n_accesos, n_cambios, n_funcionando = registrar(supabase, ahora)
    except Exception as e:
        # Queda registrado que se intentó y falló, para no confundir este
        # hueco con "no cambió nada".
        supabase.table("consultas").insert({
            "timestamp": ahora,
            "ok": False,
            "error": f"{type(e).__name__}: {e}"[:500],
        }).execute()
        print(f"[{ahora}] ERROR - {e}")
        sys.exit(1)

    supabase.table("consultas").insert({
        "timestamp": ahora,
        "ok": True,
        "accesos": n_accesos,
        "cambios": n_cambios,
        "funcionando": n_funcionando,
    }).execute()

    print(f"[{ahora}] OK - {n_accesos} accesos consultados, {n_cambios} cambios registrados")


if __name__ == "__main__":
    main()
