"""Modelo de accesibilidad de la red: qué hace falta para ir del andén a la calle.

Lee los archivos modelo/linea_*.yaml (lugares de cada estación y conexiones
por medio de elevación) y calcula, para cada andén y cada perfil de persona,
si se puede salir a la calle y entrar desde ella, y con qué equipos. El
camino puede pasar por otra estación cuando hay una combinación modelada
(por ejemplo, de Leandro N. Alem se sale por Correo Central).

Uso:
    python modelo/red.py              # reporte de todas las líneas
    python modelo/red.py H            # solo la Línea H
    python modelo/red.py H --en-vivo  # descuenta los equipos fuera de servicio
                                      # según la API de EMOVA en este momento
    python modelo/red.py --json docs/red.json   # exporta la red para la página
"""

import json
import sys
from collections import deque
from itertools import permutations
from pathlib import Path

import yaml

URL = "https://aplicacioneswp.metrovias.com.ar/APIAccesibilidad/Accesibilidad.svc/GetLineas"

# Medios que puede usar cada perfil. Las escaleras fijas no figuran en ninguna
# fuente, así que ningún perfil las contempla todavía.
PERFILES = {
    "silla_de_ruedas": {"ascensor", "salvaescaleras", "camino_rodante"},
    "sin_escaleras_fijas": {"ascensor", "salvaescaleras", "camino_rodante", "escalera_mecanica"},
}


def cargar():
    """Devuelve todas las líneas modeladas, ya validadas."""
    carpeta = Path(__file__).parent
    lineas = [yaml.safe_load(p.read_text()) for p in sorted(carpeta.glob("linea_*.yaml"))]
    for l in lineas:
        for e in l["estaciones"]:
            validar(l["linea"], e)
    return lineas


def extremos(conexion):
    return conexion["entre"] if "entre" in conexion else [conexion["desde"], conexion["hasta"]]


def validar(linea, estacion):
    """Corta si una conexión nombra un lugar que no existe o repite un equipo."""
    vistos = set()
    for c in estacion["conexiones"]:
        for lugar in extremos(c):
            if lugar not in estacion["lugares"]:
                raise ValueError(f"{linea} {estacion['nombre']}: {c['equipo']} usa un lugar inexistente: {lugar}")
        if c["equipo"] in vistos:
            raise ValueError(f"{linea} {estacion['nombre']}: equipo repetido: {c['equipo']}")
        vistos.add(c["equipo"])


class Red:
    """Grafo de toda la red. Un nodo es (línea, estación, lugar)."""

    def __init__(self, lineas=None):
        self.lineas = lineas or cargar()
        self.estaciones = {(l["linea"], e["nombre"]): e for l in self.lineas for e in l["estaciones"]}

    def nodo(self, linea, estacion, lugar):
        """Un punto de combinación con "lugar" es ese lugar de la otra estación."""
        datos = self.estaciones[(linea, estacion)]["lugares"][lugar]
        destino = (datos.get("linea"), datos.get("estacion"))
        if datos["tipo"] == "combinacion" and "lugar" in datos and destino in self.estaciones:
            return (*destino, datos["lugar"])
        return (linea, estacion, lugar)

    def tipo(self, nodo):
        return self.estaciones[nodo[:2]]["lugares"][nodo[2]]["tipo"]

    def grafo(self, perfil, fuera_de_servicio=None):
        """{nodo: [(nodo_siguiente, paso)]}, con los medios que el perfil puede usar."""
        fuera_de_servicio = fuera_de_servicio or {}
        grafo = {}
        for (linea, nombre), e in self.estaciones.items():
            caidos = fuera_de_servicio.get((linea, e.get("nombre_api", nombre)), set())
            for c in e["conexiones"]:
                if c["medio"] not in PERFILES[perfil] or c["equipo"] in caidos:
                    continue
                paso = {"linea": linea, "estacion": nombre, "equipo": c["equipo"], "medio": c["medio"]}
                tramos = permutations(c["entre"], 2) if "entre" in c else [(c["desde"], c["hasta"])]
                for a, b in tramos:
                    grafo.setdefault(self.nodo(linea, nombre, a), []).append((self.nodo(linea, nombre, b), paso))
        return grafo

    def camino(self, grafo, origen):
        """Camino con menos equipos desde el origen hasta una salida a la calle."""
        cola = deque([(origen, [])])
        visitados = {origen}
        while cola:
            nodo, pasos = cola.popleft()
            if self.tipo(nodo) == "calle":
                return {"pasos": pasos, "calle": self.estaciones[nodo[:2]]["lugares"][nodo[2]]["direccion"],
                        "por": None if nodo[:2] == origen[:2] else {"linea": nodo[0], "estacion": nodo[1]}}
            for siguiente, paso in grafo.get(nodo, []):
                if siguiente not in visitados:
                    visitados.add(siguiente)
                    cola.append((siguiente, pasos + [paso]))
        return None

    def accesos(self, linea, estacion, perfil, fuera_de_servicio=None):
        """Para cada andén en uso: cómo salir a la calle y cómo entrar desde ella."""
        ida = self.grafo(perfil, fuera_de_servicio)
        vuelta = {}
        for a, vecinos in ida.items():
            for b, paso in vecinos:
                vuelta.setdefault(b, []).append((a, paso))
        resultado = {}
        for lugar, datos in self.estaciones[(linea, estacion)]["lugares"].items():
            if datos["tipo"] != "anden" or not datos["sentidos"]:
                continue
            entrar = self.camino(vuelta, (linea, estacion, lugar))
            if entrar:
                entrar["pasos"].reverse()
            resultado[lugar] = {"salir": self.camino(ida, (linea, estacion, lugar)), "entrar": entrar}
        return resultado


def fuera_de_servicio_ahora():
    """{(letra de línea, nombre de estación en la API): {códigos fuera de servicio}}."""
    import requests

    estado = {}
    for linea in requests.get(URL, timeout=30).json():
        for estacion in linea["estaciones"]:
            estado[(linea["nombre"].replace("Línea ", ""), estacion["nombre"])] = {
                a["nombre"] for a in estacion["accesos"] if not a["funcionando"]
            }
    return estado


def situacion(estacion, accesos):
    """completo: se entra y se sale por todos los andenes; parcial: por algunos.

    En un andén solo de llegada alcanza con poder salir, y en uno solo de
    salida, con poder entrar.
    """
    completos, alguno = [], False
    for anden, r in accesos.items():
        solo = estacion["lugares"][anden].get("solo")
        necesarios = [r["salir"]] if solo == "llegada" else [r["entrar"]] if solo == "salida" else [r["salir"], r["entrar"]]
        completos.append(all(necesarios))
        alguno = alguno or any(necesarios)
    if completos and all(completos):
        return "completo"
    return "parcial" if alguno else "sin_acceso"


def describir(resultado):
    if resultado is None:
        return "NO HAY CAMINO"
    equipos = " + ".join(f"{p['medio'].replace('_', ' ')} {p['equipo']}" for p in resultado["pasos"])
    por = f"  (por {resultado['por']['estacion']}, Línea {resultado['por']['linea']})" if resultado["por"] else ""
    return f"{equipos}  ·  {resultado['calle']}{por}"


def reporte(linea=None, en_vivo=False):
    red = Red()
    estado = fuera_de_servicio_ahora() if en_vivo else {}
    for l in red.lineas:
        if linea and l["linea"].upper() != linea.upper():
            continue
        print(f"\n════ LÍNEA {l['linea']} " + ("(estado en vivo)" if en_vivo else "(si todo funcionara)") + " ════")
        for e in l["estaciones"]:
            caidos = estado.get((l["linea"], e.get("nombre_api", e["nombre"])), set())
            dudas = len(e.get("dudas", [])) + sum(1 for c in e["conexiones"] if "duda" in c)
            print(f"\n{e['nombre']}" + (f"   [{dudas} dudas a revisar]" if dudas else "")
                  + (f"   [fuera de servicio: {', '.join(sorted(caidos))}]" if caidos else ""))
            for perfil in PERFILES:
                print(f"  {perfil}")
                for anden, r in red.accesos(l["linea"], e["nombre"], perfil, estado).items():
                    print(f"    andén a {' y '.join(e['lugares'][anden]['sentidos'])}")
                    print(f"      salir:  {describir(r['salir'])}")
                    print(f"      entrar: {describir(r['entrar'])}")


def exportar(ruta):
    """Vuelca la red y los caminos calculados (sin estado en vivo) a un JSON."""
    red = Red()
    salida = []
    for l in red.lineas:
        for e in l["estaciones"]:
            accesos = {p: red.accesos(l["linea"], e["nombre"], p) for p in PERFILES}
            salida.append({
                "linea": l["linea"],
                "nombre": e["nombre"],
                "categoria_oficial": e["categoria_oficial"],
                "dudas": e.get("dudas", []),
                "lugares": e["lugares"],
                "conexiones": e["conexiones"],
                "sin_ubicar": e.get("sin_ubicar", []),
                "accesos": accesos,
                "situacion": {p: situacion(e, a) for p, a in accesos.items()},
            })
    Path(ruta).write_text(json.dumps({"perfiles": list(PERFILES), "estaciones": salida}, ensure_ascii=False, indent=1))
    print(f"{len(salida)} estaciones exportadas a {ruta}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--json" in sys.argv:
        exportar(args[0])
    else:
        reporte(args[0] if args else None, en_vivo="--en-vivo" in sys.argv)
