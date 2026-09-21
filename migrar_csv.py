"""Migración única: carga accesibilidad_emova.csv en Supabase.

Recorre los snapshots del CSV en orden cronológico y aplica la misma lógica
de detección de cambios que descargar.py (esquema normalizado: la dimensión
`accesos` se puebla primero, el historial solo referencia su id), para no
perder el historial que ya se juntó localmente antes de mudar todo a
Supabase.

Uso:
    python migrar_csv.py

Variables de entorno requeridas: SUPABASE_URL, SUPABASE_KEY
"""

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone

from supabase import create_client

CSV_FILE = "accesibilidad_emova.csv"


def to_bool(value):
    return value == "True"


def to_int_or_none(value):
    return int(value) if value not in ("", None) else None


def main():
    supabase_url = os.environ["SUPABASE_URL"]
    supabase_key = os.environ["SUPABASE_KEY"]
    supabase = create_client(supabase_url, supabase_key)

    snapshots = defaultdict(list)
    dimension_por_key = {}

    with open(CSV_FILE, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            snapshots[row["timestamp"]].append(row)
            key = (row["linea"], row["estacion"], row["nombre"])
            if key not in dimension_por_key:
                dimension_por_key[key] = {
                    "linea": row["linea"],
                    "descripcion_linea": row["descripcion_linea"] or None,
                    "estacion": row["estacion"],
                    "nombre": row["nombre"],
                    "descripcion": row["descripcion"] or None,
                    "tipo": to_int_or_none(row["tipo"]),
                }

    print(f"{len(dimension_por_key)} accesos distintos encontrados, sincronizando catálogo...")
    supabase.table("accesos").upsert(
        list(dimension_por_key.values()), on_conflict="linea,estacion,nombre"
    ).execute()

    catalogo = supabase.table("accesos").select("id,linea,estacion,nombre").execute().data
    id_por_key = {(r["linea"], r["estacion"], r["nombre"]): r["id"] for r in catalogo}

    timestamps_ordenados = sorted(snapshots.keys())
    print(f"{len(timestamps_ordenados)} snapshots encontrados en {CSV_FILE}")

    estado_actual = {}
    total_cambios = 0
    ultimo_upsert = None

    for ts in timestamps_ordenados:
        ts_iso = datetime.fromisoformat(ts).astimezone(timezone.utc).isoformat()
        cambios = []
        upserts = []

        for row in snapshots[ts]:
            key = (row["linea"], row["estacion"], row["nombre"])
            acceso_id = id_por_key[key]
            estado_nuevo = to_int_or_none(row["estado"])
            funcionando_nuevo = to_bool(row["funcionando"])
            previo = estado_actual.get(acceso_id)

            cambio = (
                previo is None
                or previo["estado"] != estado_nuevo
                or previo["funcionando"] != funcionando_nuevo
            )

            if cambio:
                cambios.append({
                    "acceso_id": acceso_id,
                    "estado_anterior": previo["estado"] if previo else None,
                    "estado_nuevo": estado_nuevo,
                    "funcionando_anterior": previo["funcionando"] if previo else None,
                    "funcionando_nuevo": funcionando_nuevo,
                    "timestamp": ts_iso,
                    "fecha_actualizacion_api": None,
                })
                total_cambios += 1

            ultimo_cambio = ts_iso if cambio else (previo["ultimo_cambio"] if previo else ts_iso)
            estado_actual[acceso_id] = {
                "estado": estado_nuevo,
                "funcionando": funcionando_nuevo,
                "ultimo_cambio": ultimo_cambio,
            }

            upserts.append({
                "acceso_id": acceso_id,
                "estado": estado_nuevo,
                "funcionando": funcionando_nuevo,
                "fecha_actualizacion_api": None,
                "ultima_consulta": ts_iso,
                "ultimo_cambio": ultimo_cambio,
            })

        if cambios:
            supabase.table("estado_historial").insert(cambios).execute()

        ultimo_upsert = upserts
        print(f"[{ts}] {len(cambios)} cambios")

    # Solo hace falta upsertear estado_actual con el último snapshot.
    if ultimo_upsert:
        supabase.table("estado_actual").upsert(ultimo_upsert, on_conflict="acceso_id").execute()

    print(f"\nListo. {total_cambios} cambios migrados al historial.")


if __name__ == "__main__":
    main()
