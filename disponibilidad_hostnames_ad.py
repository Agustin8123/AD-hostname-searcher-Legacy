#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Buscador de nombres de host disponibles en Active Directory.

Ver readme.txt para la explicacion completa
"""

import argparse
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional


# ------------------------------------------------------------------------
# 1. NOMENCLATURA: prefijo fijo + familias conocidas
# ------------------------------------------------------------------------
# Si mas adelante aparece una tercera familia, alcanza con agregarla a FAMILIAS: el PATRON se arma
# solo a partir de esta lista.

PREFIJO = "ATZZTE"
FAMILIAS = ["PC", "NB", "TA"]

# Cantidad de digitos que ocupa el numero de maquina (la parte variable
# del nombre, despues del "000" fijo). HOY son 3 digitos porque asi es
# la nomenclatura real. Si el dia de manana la empresa pasa a usar mas
# digitos, este es el UNICO numero que hay que cambiar: el patron y los
# limites de --maximo se recalculan solos
DIGITOS_NUMERO = 3
NUMERO_MINIMO = 10
NUMERO_MAXIMO = 10 ** DIGITOS_NUMERO - 1  # 999 mientras DIGITOS_NUMERO sea 3

# Arma el patron dinamicamente, por ejemplo: ^ATZZTE(PC|NB)000(\d{3})$
#   grupo 1 = familia (PC / NB)
#   grupo 2 = numero de maquina (DIGITOS_NUMERO digitos)
PATRON = re.compile(rf"^{PREFIJO}({'|'.join(FAMILIAS)})000(\d{{{DIGITOS_NUMERO}}})$")

# Configuracion del ping de verificacion complementaria.
PING_TIMEOUT_MS = 500
PING_INTENTOS = 1

# Cuantos pings se hacen EN PARALELO como maximo. Con cientos de equipos,
# hacerlos de a uno sería lento (cada ping puede tardar hasta
# PING_TIMEOUT_MS si no responde). 
MAX_PINGS_CONCURRENTES = 999


# ------------------------------------------------------------------------
# 2. ESTRUCTURA DE DATOS
# ------------------------------------------------------------------------

@dataclass
class Equipo:
    """Un nombre dentro de una familia, este o no registrado en AD."""
    nombre: str
    familia: str
    numero: int
    en_ad: bool
    responde_red: Optional[bool] = None  # None = no se verifico por red


# ------------------------------------------------------------------------
# 3. OBTENER LA LISTA DE COMPUTADORAS (AD real o datos de demo)
# ------------------------------------------------------------------------

def obtener_computadoras_ad() -> list[str]:
    """
    Ejecuta PowerShell para pedirle a Active Directory los nombres de
    computadora del dominio. Usa la sesion de Windows actual (no pide
    usuario ni contrasena).

    "Import-Module ActiveDirectory -ErrorAction Stop" se agrega antes de
    la consulta para que, si el modulo no esta instalado, el error sea
    claro".
    """
    comando = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Import-Module ActiveDirectory -ErrorAction Stop; "
        "Get-ADComputer -Filter * | Select-Object -ExpandProperty Name",
    ]

    try:
        resultado = subprocess.run(comando, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError(
            "No se encontro el ejecutable 'powershell'. Este script debe "
            "correr en Windows (o en una maquina con PowerShell disponible)."
        )

    if resultado.returncode != 0:
        raise RuntimeError(
            "No se pudo consultar Active Directory.\n"
            "Posibles causas: el modulo 'ActiveDirectory' de PowerShell no "
            "esta instalado (RSAT), no hay conexion con el dominio, o la "
            "cuenta actual no tiene permisos de lectura sobre AD.\n\n"
            f"Detalle de PowerShell:\n{resultado.stderr.strip()}"
        )

    # Cada linea de salida de PowerShell es un nombre de computadora.
    return [linea.strip() for linea in resultado.stdout.splitlines() if linea.strip()]


def obtener_computadoras_demo() -> list[str]:
    """
    Lista de ejemplo para
    poder probar toda la logica sin necesitar un AD real.
    Faltan a proposito ATZZTEPC000004 y ATZZTENB000002.
    """
    return [
        "ATZZTEPC000001",
        "ATZZTEPC000002",
        "ATZZTEPC000003",
        "ATZZTEPC000005",
        "ATZZTENB000001",
        "ATZZTENB000003",
        "ATZZTENB000004",
    ]


# ------------------------------------------------------------------------
# 4. RECONOCER LA NOMENCLATURA Y AGRUPAR POR FAMILIA
# ------------------------------------------------------------------------

def procesar_nombre(nombre: str) -> Optional[tuple[str, int]]:
    """
    Compara 'nombre' contra el patron reglamentario.
    Devuelve (familia, numero) si coincide, o None si no coincide.

    """
    coincidencia = PATRON.match(nombre.strip().upper())
    if not coincidencia:
        return None
    familia = coincidencia.group(1)
    numero = int(coincidencia.group(2))
    if numero == 0:
        return None
    return familia, numero


def clasificar_por_familia(nombres: list[str]) -> tuple[dict[str, dict[int, str]], list[str]]:
    """
    Agrupa los nombres por familia.
    Devuelve (ocupados, fuera_de_norma) donde:
      ocupados = {"PC": {1: "ATZZTEPC000001", ...}, "NB": {...}}
      fuera_de_norma = nombres que no coinciden con ninguna familia conocida
    """
    ocupados: dict[str, dict[int, str]] = {familia: {} for familia in FAMILIAS}
    fuera_de_norma: list[str] = []

    for nombre in nombres:
        resultado = procesar_nombre(nombre)
        if resultado is None:
            fuera_de_norma.append(nombre)
            continue
        familia, numero = resultado
        ocupados[familia][numero] = nombre

    return ocupados, fuera_de_norma


# ------------------------------------------------------------------------
# 5. DETECTAR HUECOS (numeros potencialmente disponibles)
# ------------------------------------------------------------------------

def detectar_huecos(
    numeros_ocupados: dict[int, str],
    rango_forzado: Optional[tuple[int, int]],
) -> list[int]:
    """
    A partir de los numeros ya ocupados de una familia, devuelve los que
    faltan dentro del rango a analizar.

    Sin 'rango_forzado', el rango es NUMERO_MINIMO hasta el mas alto
    encontrado en AD. Con 'rango_forzado' = (inicio, fin), se usa ese
    tramo exacto en su lugar -- por ejemplo (20, 124) para buscar huecos
    solo entre 020 y 124, sin importar que haya en AD fuera de ese tramo.

    """
    if not numeros_ocupados and rango_forzado is None:
        return []
    if rango_forzado is not None:
        inicio, fin = rango_forzado
    else:
        inicio, fin = NUMERO_MINIMO, max(numeros_ocupados)
    return [n for n in range(inicio, fin + 1) if n not in numeros_ocupados]


# ------------------------------------------------------------------------
# 6. VERIFICACION COMPLEMENTARIA POR RED (ping)
# ------------------------------------------------------------------------

def comprobar_ping(host: str) -> bool:
    """
    Ping breve a 'host'. True si responde, False si no.

    """
    comando = ["ping", "-n", str(PING_INTENTOS), "-w", str(PING_TIMEOUT_MS), host]

    try:
        resultado = subprocess.run(
            comando,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return resultado.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False


# ------------------------------------------------------------------------
# 7. ARMAR EL REPORTE (ocupados + huecos, con estado de red opcional)
# ------------------------------------------------------------------------

def construir_reporte(
    ocupados_por_familia: dict[str, dict[int, str]],
    verificar_red: bool,
    rango_forzado: Optional[tuple[int, int]],
) -> dict[str, list[Equipo]]:
    """
    Para cada familia, arma la lista completa de equipos dentro del rango
    a analizar, marcando cuales estan en AD y (si corresponde) si
    responden en la red.

    El armado se hace en dos pasadas:
      1) Se decide, para cada numero, el nombre y si esta en AD (esto es
         puro calculo, no toca la red, y es practicamente instantaneo
         aunque haya miles de equipos).
      2) Si hay que verificar la red, se pingean TODOS los nombres a la
         vez usando un pool de hilos (ver MAX_PINGS_CONCURRENTES).
    """
    # Paso 1: armar, por familia, la lista de (numero, nombre, en_ad).
    pendientes: dict[str, list[tuple[int, str, bool]]] = {}

    for familia, ocupados in ocupados_por_familia.items():
        if not ocupados and rango_forzado is None:
            pendientes[familia] = []
            continue

        if rango_forzado is not None:
            inicio, fin = rango_forzado
        else:
            inicio, fin = NUMERO_MINIMO, max(ocupados)
        huecos = set(detectar_huecos(ocupados, rango_forzado))

        lista = []
        for numero in range(inicio, fin + 1):
            en_ad = numero not in huecos
            nombre = ocupados[numero] if en_ad else f"{PREFIJO}{familia}000{numero:0{DIGITOS_NUMERO}d}"
            lista.append((numero, nombre, en_ad))
        pendientes[familia] = lista

    # Paso 2: pingear todos los nombres de todas las familias en paralelo.
    resultados_ping: dict[str, bool] = {}
    if verificar_red:
        todos_los_nombres = [nombre for lista in pendientes.values() for (_, nombre, _) in lista]
        if todos_los_nombres:
            hilos = min(MAX_PINGS_CONCURRENTES, len(todos_los_nombres))
            with ThreadPoolExecutor(max_workers=hilos) as pool:
                respuestas = pool.map(comprobar_ping, todos_los_nombres)
                resultados_ping = dict(zip(todos_los_nombres, respuestas))

    # Paso 3: combinar todo en los objetos Equipo finales.
    reporte: dict[str, list[Equipo]] = {}
    for familia, lista in pendientes.items():
        equipos = []
        for numero, nombre, en_ad in lista:
            responde = resultados_ping.get(nombre) if verificar_red else None
            equipos.append(Equipo(
                nombre=nombre, familia=familia, numero=numero,
                en_ad=en_ad, responde_red=responde,
            ))
        reporte[familia] = equipos

    return reporte

# ------------------------------------------------------------------------
# 8. MOSTRAR EL RESULTADO EN CONSOLA
# ------------------------------------------------------------------------

def mostrar_reporte(
    reporte: dict[str, list[Equipo]],
    fuera_de_norma: list[str],
    solo_libres: bool = False,
) -> None:
    ancho = 60
    print("=" * ancho)
    print("DISPONIBILIDAD DE EQUIPOS".center(ancho))
    print("=" * ancho)

    primer_disponible: dict[str, Optional[str]] = {familia: None for familia in reporte}

    for familia, equipos in reporte.items():
        print()
        if not equipos:
            print(f"  (Sin equipos de la familia {familia} en AD)")
            continue

        equipos_mostrados = 0
        for equipo in equipos:
            estado_ad = "OCUPADO" if equipo.en_ad else "LIBRE  "

            if equipo.responde_red is None:
                estado_red = "SIN VERIFICAR"
            elif equipo.en_ad:
                # Ocupado en AD: el ping solo dice si esta prendido o apagado.
                estado_red = "ACTIVO " if equipo.responde_red else "APAGADO"
            else:
                # Libre en AD pero responde al ping -> posible conflicto,
                # conviene revisarlo a mano antes de asignar el nombre.
                estado_red = "CONFLICTO" if equipo.responde_red else "LIBRE  "

            es_conflicto = (not equipo.en_ad) and (equipo.responde_red is True)

            # El "primer disponible" se calcula sobre TODOS los equipos,
            # aunque --solo-libres despues filtre lo que se imprime.
            if not equipo.en_ad and not es_conflicto and primer_disponible[familia] is None:
                primer_disponible[familia] = equipo.nombre

            if solo_libres and equipo.en_ad:
                continue

            print(f"{equipo.nombre}   AD: {estado_ad}   RED: {estado_red}")
            equipos_mostrados += 1

        if solo_libres and equipos_mostrados == 0:
            print(f"  (Sin nombres libres para la familia {familia} en el rango analizado)")

    print()
    print("-" * ancho)
    for familia, nombre in primer_disponible.items():
        print(f"Primer {familia} disponible: {nombre or 'Ninguno encontrado'}")
    print("-" * ancho)

    if fuera_de_norma:
        print()
        print("Nombres en AD que no siguen la nomenclatura reglamentaria:")
        for nombre in fuera_de_norma:
            print(f"  - {nombre}")

# ------------------------------------------------------------------------
# 9. VALIDACION DE ENTRADA (la usan --rango y el menu interactivo)
# ------------------------------------------------------------------------

def validar_rango(valor: str) -> tuple[int, int]:
    """
    Valida el formato de --rango, por ejemplo "020-124".
    Devuelve (inicio, fin) como enteros, ya validados contra
    NUMERO_MINIMO/NUMERO_MAXIMO.

    """
    partes = valor.split("-")
    if len(partes) != 2:
        raise argparse.ArgumentTypeError(
            f"El rango debe tener el formato INICIO-FIN, por ejemplo 020-124 "
            f"(recibido: {valor!r})"
        )
    try:
        inicio, fin = int(partes[0]), int(partes[1])
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"El rango debe tener dos numeros enteros separados por un guion "
            f"(recibido: {valor!r})"
        )
    if not (NUMERO_MINIMO <= inicio <= NUMERO_MAXIMO):
        raise argparse.ArgumentTypeError(
            f"El inicio del rango debe estar entre {NUMERO_MINIMO} y "
            f"{NUMERO_MAXIMO} (recibido: {inicio})"
        )
    if not (NUMERO_MINIMO <= fin <= NUMERO_MAXIMO):
        raise argparse.ArgumentTypeError(
            f"El fin del rango debe estar entre {NUMERO_MINIMO} y "
            f"{NUMERO_MAXIMO} (recibido: {fin})"
        )
    if inicio > fin:
        raise argparse.ArgumentTypeError(
            f"El inicio del rango ({inicio}) no puede ser mayor que el fin ({fin})"
        )
    return inicio, fin

# ------------------------------------------------------------------------
# 10. INTERFAZ DE TEXTO INTERACTIVA (TUI)
# ------------------------------------------------------------------------

def preguntar_opcion(titulo: str, opciones: list[str]) -> int:
    """Muestra un menu numerado y devuelve el indice (0-based) elegido."""
    print(f"\n{titulo}")
    for i, opcion in enumerate(opciones, start=1):
        print(f"  [{i}] {opcion}")
    while True:
        eleccion = input(f"Elegi una opcion (1-{len(opciones)}): ").strip()
        if eleccion.isdigit() and 1 <= int(eleccion) <= len(opciones):
            return int(eleccion) - 1
        print("Opcion invalida, proba de nuevo.")


def preguntar_si_no(pregunta: str, por_defecto: bool) -> bool:
    """Pregunta s/n; Enter en vacio usa 'por_defecto'."""
    sufijo = "[S/n]" if por_defecto else "[s/N]"
    while True:
        respuesta = input(f"{pregunta} {sufijo}: ").strip().lower()
        if respuesta == "":
            return por_defecto
        if respuesta in ("s", "si", "y", "yes"):
            return True
        if respuesta in ("n", "no"):
            return False
        print("Respuesta invalida: escribi 's' o 'n'.")


def preguntar_rango_manual() -> tuple[int, int]:
    """Pide el rango a mano, con la MISMA validacion que usa --rango."""
    while True:
        texto = input(
            f"Rango, formato INICIO-FIN (ej. 020-124, entre "
            f"{NUMERO_MINIMO} y {NUMERO_MAXIMO}): "
        ).strip()
        try:
            return validar_rango(texto)
        except argparse.ArgumentTypeError as error:
            print(f"  {error}")


def ejecutar_tui() -> None:
    """Bucle principal del menu interactivo (se activa con --tui)."""
    ancho = 60
    while True:
        print("=" * ancho)
        print("DISPONIBILIDAD DE EQUIPOS EN AD".center(ancho))
        print("=" * ancho)

        usar_demo = preguntar_opcion(
            "Fuente de datos:",
            ["Datos de ejemplo (demo)",
             "Active Directory"],
        ) == 0

        verificar_red = preguntar_si_no("\nVerificar tambien la red (ping)?", por_defecto=True)

        eleccion_rango = preguntar_opcion(
            "Rango de numeros a analizar:",
            [f"Automatico (desde {NUMERO_MINIMO:0{DIGITOS_NUMERO}d} hasta el mas alto en AD)",
             f"Completo ({NUMERO_MINIMO:0{DIGITOS_NUMERO}d}-{NUMERO_MAXIMO})",
             "Especifico (elegis inicio y fin, ej. 020-124)"],
        )
        if eleccion_rango == 0:
            rango_forzado = None
        elif eleccion_rango == 1:
            rango_forzado = (NUMERO_MINIMO, NUMERO_MAXIMO)
        else:
            rango_forzado = preguntar_rango_manual()

        solo_libres = preguntar_si_no("\nMostrar unicamente los nombres libres?", por_defecto=False)

        print()
        if usar_demo:
            print("[MODO DEMO] Usando datos de ejemplo, no se consulta AD real.\n")
            nombres = obtener_computadoras_demo()
        else:
            print("Consultando Active Directory...\n")
            try:
                nombres = obtener_computadoras_ad()
            except RuntimeError as error:
                print(error)
                if preguntar_si_no("\nQueres intentar de nuevo?", por_defecto=True):
                    print()
                    continue
                return

        ocupados_por_familia, fuera_de_norma = clasificar_por_familia(nombres)

        if rango_forzado is not None:
            inicio_forzado, fin_forzado = rango_forzado
            for familia, ocupados in ocupados_por_familia.items():
                fuera_de_rango = sorted(n for n in ocupados if n < inicio_forzado or n > fin_forzado)
                if fuera_de_rango:
                    print(
                        f"\nAviso: {len(fuera_de_rango)} equipo(s) de {familia} en AD "
                        f"quedan fuera del rango pedido y no van a aparecer abajo."
                    )

        if verificar_red:
            print("\nVerificando estado de red (puede tardar unos segundos)...\n")
        else:
            print()

        reporte = construir_reporte(
            ocupados_por_familia,
            verificar_red=verificar_red,
            rango_forzado=rango_forzado,
        )

        print()
        mostrar_reporte(reporte, fuera_de_norma, solo_libres=solo_libres)

        if not preguntar_si_no("\nHacer otra busqueda?", por_defecto=False):
            print("\nListo.")
            return
        print()

# ------------------------------------------------------------------------
# 11. PROGRAMA PRINCIPAL
# ------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Busca nombres de host disponibles en Active Directory "
                     "segun la nomenclatura ATZZTEPC000xxx / ATZZTENB000xxx."
    )
    parser.add_argument("--tui", action="store_true",
                         help="Abre un menu interactivo en vez de usar los flags de abajo.")
    parser.add_argument("--demo", action="store_true",
                         help="Usa datos de ejemplo")
    parser.add_argument("--sin-red", action="store_true",
                         help="No hace ping a los equipos (mas rapido).")
    parser.add_argument("--maximo", type=int, default=None,
                         help=f"Fuerza el numero maximo de busqueda de huecos por familia "
                              f"({NUMERO_MINIMO}-{NUMERO_MAXIMO}). Equivale a "
                              f"--rango {NUMERO_MINIMO}-N. No se combina con --rango.")
    parser.add_argument("--rango", type=validar_rango, default=None, metavar="INICIO-FIN",
                         help=f"Busca huecos solo en ese tramo, por ejemplo 020-124 "
                              f"(entre {NUMERO_MINIMO} y {NUMERO_MAXIMO}). No se "
                              f"combina con --maximo.")
    parser.add_argument("--solo-libres", action="store_true",
                         help="Muestra unicamente los nombres libres en el listado.")
    args = parser.parse_args()

    if args.tui:
        ejecutar_tui()
        return

    if args.maximo is not None and args.rango is not None:
        parser.error("No se pueden usar --maximo y --rango juntos; elegi uno de los dos.")

    # El tope no esta escrito a mano: sale de NUMERO_MINIMO/NUMERO_MAXIMO,
    # que a su vez salen de DIGITOS_NUMERO (ver seccion 1). Si la
    # nomenclatura cambia de longitud, este chequeo se ajusta solo.
    if args.maximo is not None and not (NUMERO_MINIMO <= args.maximo <= NUMERO_MAXIMO):
        parser.error(
            f"--maximo debe estar entre {NUMERO_MINIMO} y {NUMERO_MAXIMO} "
            f"(la nomenclatura usa exactamente {DIGITOS_NUMERO} digitos)."
        )

    # --maximo es simplemente un --rango con el inicio fijo en el minimo.
    # A partir de aca, todo el resto del programa solo conoce rango_forzado.
    if args.rango is not None:
        rango_forzado = args.rango
    elif args.maximo is not None:
        rango_forzado = (NUMERO_MINIMO, args.maximo)
    else:
        rango_forzado = None

    if args.demo:
        print("[MODO DEMO] Usando datos de ejemplo\n")
        nombres = obtener_computadoras_demo()
    else:
        print("Consultando Active Directory...\n")
        try:
            nombres = obtener_computadoras_ad()
        except RuntimeError as error:
            print(error)
            return

    ocupados_por_familia, fuera_de_norma = clasificar_por_familia(nombres)

    if rango_forzado is not None:
        inicio_forzado, fin_forzado = rango_forzado
        for familia, ocupados in ocupados_por_familia.items():
            fuera_de_rango = sorted(n for n in ocupados if n < inicio_forzado or n > fin_forzado)
            if fuera_de_rango:
                print(
                    f"Aviso: {len(fuera_de_rango)} equipo(s) de {familia} en AD "
                    f"quedan fuera del rango {inicio_forzado:0{DIGITOS_NUMERO}d}-"
                    f"{fin_forzado:0{DIGITOS_NUMERO}d} pedido y no van a aparecer "
                    f"en este reporte.\n"
                )

    if not args.sin_red:
        print("Verificando estado de red (puede tardar unos segundos)...\n")

    reporte = construir_reporte(
        ocupados_por_familia,
        verificar_red=not args.sin_red,
        rango_forzado=rango_forzado,
    )

    mostrar_reporte(reporte, fuera_de_norma, solo_libres=args.solo_libres)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nCancelado por el usuario.")
