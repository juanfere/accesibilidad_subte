"""Exporta las dudas del modelo a un CSV para revisarlas en una planilla.

Una fila por cosa a verificar en el lugar, con una pregunta de sí/no y
columnas vacías para anotar lo que se ve en la estación.

Uso:
    python modelo/exportar_dudas.py modelo/dudas.csv
"""

import csv
import re
import sys

from red import Red, nombre_equipo

SIN_CALLE = "¿Hay algún ascensor o escalera mecánica que llegue a la calle?"
CERRADA = "¿La estación sigue cerrada por obras?"

# Preguntas de sí/no para las dudas de estación, en el mismo orden en que
# están escritas en el archivo de la línea. Una lista vacía = no es una duda
# sino un aviso (obra en curso, equipo fuera de servicio), y no se exporta.
PREGUNTAS = {
    ("A", "Perú"): [["¿Se puede ir del hall boletería sur (entrada de Av. de Mayo 556) al andén sin usar escaleras fijas?"]],
    ("A", "Pasco"): [["¿El único andén en uso es el que va a Plaza de Mayo?"]],
    ("A", "Alberti"): [["¿El único andén en uso es el que va a San Pedrito?"]],
    ("A", "Plaza Miserere"): [[SIN_CALLE, "¿Cada sentido de viaje usa un solo andén?"]],
    ("B", "Leandro N. Alem"): [["¿Hay un ascensor que une este andén con el de Correo Central (Línea E)?"]],
    ("B", "Florida"): [[SIN_CALLE, "¿Las escaleras N°19 y N°20 son las del vestíbulo este, y las N°21 y N°22 las del oeste?"]],
    ("B", "Carlos Pellegrini"): [[SIN_CALLE, "¿Hay algún ascensor o escalera mecánica hacia las combinaciones con las líneas C y D?"]],
    ("B", "Callao"): [[]],
    ("B", "Pueyrredón"): [[]],  # se pregunta en Corrientes (H)
    ("B", "Federico Lacroze"): [[]],
    ("B", "Echeverría"): [["¿El entrepiso este es el del andén a Leandro N. Alem, y el oeste el del andén a Rosas?"]],
    ("B", "Juan Manuel de Rosas - Villa Urquiza"): [["¿Las escaleras N°1, N°4, N°5, N°8 y N°9 son las del sector este, cerrado por obras?"]],
    ("C", "Retiro"): [["¿El andén y el hall boletería están al mismo nivel, sin escalones?"],
                      ["¿El Ascensor N°3 de Retiro (Línea E) llega al andén central de la Línea C?"]],
    ("C", "Lavalle"): [[CERRADA]],
    ("C", "Diagonal Norte"): [["¿El hall de la salida Sarmiento está al mismo nivel que alguno de los dos vestíbulos (norte o sur)?"], []],
    ("C", "Constitución"): [["¿Los andenes laterales (1 y 2) son solo de llegada, y el central solo de salida?"],
                            ["¿Los ascensores N°2, N°3 y N°4 paran también en el Centro de Trasbordo?"]],
    ("D", "Catedral"): [["¿El andén norte es solo de llegada, y el sur solo de salida?"]],
    ("D", "9 de Julio"): [["¿La Escalera N°2 sale del andén de la Línea C que va a Retiro? (si no, del que va a Constitución)"]],
    ("D", "Tribunales - Teatro Colón"): [[CERRADA]],
    ("D", "Pueyrredón"): [["¿El ascensor de combinación con Santa Fe (Línea H) llega al andén central de Pueyrredón?"]],
    ("D", "Olleros"): [[]],
    ("D", "José Hernández"): [[]],
    ("E", "Retiro"): [["¿Se puede ir sin escalones del vestíbulo (donde deja el Ascensor N°1) a la boletería (de donde sale el Ascensor N°2)?"], []],
    ("E", "Catalinas"): [["¿El Ascensor N°1 (del andén) y el Ascensor N°2 (a la calle) llegan al mismo vestíbulo?"]],
    ("E", "Correo Central"): [["¿Las escaleras N°4 y N°5 son las del vestíbulo sur, y las N°6 y N°7 las del norte?",
                               "¿La Escalera N°4 baja (del vestíbulo al andén) y la N°5 sube?"]],
    ("E", "Jujuy"): [[SIN_CALLE], ["¿El ascensor de combinación con Humberto 1° (Línea H) llega al andén a Plaza de los Virreyes?"]],
    ("E", "Plaza de los Virreyes"): [["¿Se usa un solo andén para llegar y para salir?", "¿Hay algún ascensor o escalera mecánica en la estación?"]],
    ("H", "Facultad de Derecho"): [["¿Se usa siempre el mismo andén para llegar y para salir?"]],
    ("H", "Santa Fe - Carlos Jáuregui"): [["¿Se puede llegar al pasillo de combinación con la Línea D sin usar escaleras fijas?"], []],
    ("H", "Corrientes"): [["¿Las escaleras N°7 y N°10 van al hall boletería (entrepiso sur), y las N°8 y N°9 al entrepiso norte?"],
                          ["¿Los ascensores N°4, N°5 y N°6 llegan hasta los andenes de Pueyrredón (Línea B)?"]],
    ("H", "Once - 30 de Diciembre"): [["¿El número y el recorrido de cada escalera coinciden con lo que dice el mapa?"]],
    ("H", "Inclán - Mezquita Al Ahmad"): [["¿Las boleterías del lado Inclán y del lado Garay están unidas sin escalones?"]],
    ("H", "Hospitales"): [["¿El Ascensor N°3 va al andén que se usa para llegar y salir?"]],
}

COLUMNAS = [
    "ID", "Línea", "Estación", "Qué se revisa", "Equipo (como está señalizado)", "Código EMOVA", "Texto de EMOVA",
    "Según el mapa: desde", "Según el mapa: hasta", "Según el mapa: sentido", "Duda", "Pregunta",
    # Para completar en la planilla:
    "Respuesta (sí/no)", "¿Sale de o llega a un andén? (sí/no)", "¿Andén hacia dónde?", "¿Llega a la calle? (sí/no)",
    "¿Por qué dirección sale?", "¿Sube, baja o las dos?", "Notas / qué corregir", "Revisó", "Fecha",
]

ALTURA = {"anden": 0, "interior": 1, "calle": 2}


def nombre_lugar(estacion, lugar):
    datos = estacion["lugares"][lugar]
    if datos.get("nombre"):
        return datos["nombre"]
    if datos["tipo"] == "calle":
        return "Calle: " + datos["direccion"]
    if datos["tipo"] == "anden":
        return "Andén a " + " y ".join(datos["sentidos"]) if datos["sentidos"] else "Andén"
    return lugar


def tramo(estacion, c):
    """(desde, hasta, sentido, pregunta) de una conexión, en palabras."""
    if "entre" in c:
        lugares = [nombre_lugar(estacion, l) for l in c["entre"]]
        return lugares[0], " / ".join(lugares[1:]), "Ida y vuelta", f"¿Une «{lugares[0]}» con «{' y '.join(lugares[1:])}»?"
    desde, hasta = nombre_lugar(estacion, c["desde"]), nombre_lugar(estacion, c["hasta"])
    a, b = (ALTURA.get(estacion["lugares"][c[k]]["tipo"]) for k in ("desde", "hasta"))
    sentido = "Un solo sentido" if a is None or b is None or a == b else "Sube" if b > a else "Baja"
    return desde, hasta, sentido, f"¿Va de «{desde}» a «{hasta}»?"


def filas():
    for l in Red().lineas:
        for e in l["estaciones"]:
            base = {"Línea": l["linea"], "Estación": e["nombre"]}
            preguntas = PREGUNTAS.get((l["linea"], e["nombre"]))
            for i, duda in enumerate(e.get("dudas", [])):
                if preguntas is None and duda.startswith("Ninguna fuente informa equipos que lleguen a la calle"):
                    de_esta = [SIN_CALLE]
                else:
                    de_esta = preguntas[i]  # KeyError/IndexError = falta escribir la pregunta de una duda nueva
                for pregunta in de_esta:
                    yield {**base, "Qué se revisa": "Estación", "Duda": duda, "Pregunta": pregunta}
            dudas = {nombre_equipo(c): c["duda"] for c in e["conexiones"] if "duda" in c}
            for c in e["conexiones"]:
                if "duda" not in c:
                    continue
                # "Ídem Ascensor N°2." -> el texto de la duda a la que remite
                m = re.fullmatch(r"Ídem (.+)\.", c["duda"])
                desde, hasta, sentido, pregunta = tramo(e, c)
                yield {**base, "Qué se revisa": "Equipo", "Equipo (como está señalizado)": nombre_equipo(c),
                       "Código EMOVA": c["equipo"], "Texto de EMOVA": c["texto"], "Según el mapa: desde": desde,
                       "Según el mapa: hasta": hasta, "Según el mapa: sentido": sentido,
                       "Duda": dudas[m.group(1)] if m else c["duda"], "Pregunta": pregunta}
            for c in e.get("sin_ubicar", []):
                yield {**base, "Qué se revisa": "Equipo sin ubicar", "Equipo (como está señalizado)": nombre_equipo(c),
                       "Código EMOVA": c["equipo"], "Texto de EMOVA": c["texto"], "Duda": c["motivo"],
                       "Pregunta": "¿El equipo existe en la estación? (anotar en las columnas siguientes qué une)"}


def exportar(ruta):
    todas = list(filas())
    with open(ruta, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS)
        w.writeheader()
        for n, fila in enumerate(todas, 1):
            w.writerow({"ID": f"{fila['Línea']}-{n:03d}", **fila})
    print(f"{len(todas)} filas exportadas a {ruta}")


if __name__ == "__main__":
    exportar(sys.argv[1])
