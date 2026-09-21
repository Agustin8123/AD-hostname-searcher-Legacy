========================================================================
 Buscador de nombres de host disponibles en Active Directory

QUE HACE ESTE SCRIPT
---------------------
1. Consulta Active Directory (via PowerShell) para obtener todos los
   objetos de computadora registrados en el dominio.
2. Reconoce la nomenclatura reglamentaria
3. Detecta los numeros "huecos" (no usados) dentro de cada familia: los
   nombres que, segun AD, estarian disponibles para un equipo nuevo.
4. Hace una verificacion complementaria por red (ping), tanto de los
   nombres ocupados como de los huecos, SOLO como dato extra: la
   decision de "disponible / ocupado" se basa siempre en Active
   Directory, nunca en si responde o no el ping.

QUE NO HACE
----------------------------
- No modifica nada en Active Directory. Solo lee (Get-ADComputer).
- No guarda ni pide contrasenas: usa la sesion de Windows actual.

ARCHIVOS INCLUIDOS
---------------------
- disponibilidad_hostnames_ad.py   El script principal.
- Abrir_TUI.bat                    Doble click para abrir el menu
                                    interactivo (--tui) sin escribir nada
                                    en una terminal. Tiene que estar en
                                    la misma carpeta que el .py.
- README.md                         Este archivo.
- Dependencias de poweShell.md      Pequeño instructivo sobre como instalar
                                    las herramientas de CLI de AD. Necesarias
                                    para el script.

REQUISITOS PARA USARLO CON UN AD
--------------------------------------------
- Windows, en un equipo unido al dominio (o con el modulo de PowerShell
  "ActiveDirectory" / RSAT instalado).
- Python 3.9 o superior (no usa librerias externas, solo la libreria
  estandar). Link de descarga: https://www.python.org/downloads/
- Una cuenta de dominio con permisos de LECTURA sobre los objetos de
  computadora (la mayoria de las cuentas normales ya los tienen).

COMO PROBARLO
-------------------------------------------------------
    python disponibilidad_hostnames_ad.py --demo --sin-red

Esto usa una lista de nombres fija (identica al ejemplo del documento de
la pasantia) en lugar de llamar a PowerShell, y "--sin-red" evita los
pings (que de todas formas fallarian contra nombres que no existen en tu
red). Sirve para validar el reconocimiento de nomenclatura y la deteccion
de huecos antes de tener el laboratorio de AD armado.

USO NORMAL CON UN AD
----------------------------------
    python disponibilidad_hostnames_ad.py

MODO INTERACTIVO (TUI)
----------------------------------------------------------
    python disponibilidad_hostnames_ad.py --tui

O directamente doble click en Abrir_TUI.bat.

Abre un menu que pregunta paso a paso: fuente de datos (demo o AD real),
si verificar la red, que rango de numeros analizar (automatico, completo,
o un tramo especifico como 020-124), y si mostrar todo o solo lo libre.
Al final arma exactamente los mismos parametros que se pasarian por
linea de comandos.

OPCIONES
-----------
    --tui           Abre el menu interactivo de arriba, en vez de leer
                     los flags de abajo.
    --demo          Usa datos de ejemplo en vez de consultar AD real.
    --sin-red       No hace ping a los equipos (mas rapido).
    --maximo N      Fuerza el limite superior de busqueda de huecos a N
                     (equivale a --rango NUMERO_MINIMO-N). No se combina
                     con --rango.
    --rango I-F     Busca huecos solo entre I y F sin importar el numero
                    mas alto que haya en AD. No se combina con --maximo.
    --solo-libres   En el listado, muestra unicamente los nombres libres.

SOBRE LA CANTIDAD DE EQUIPOS
----------------------------------
Consultar AD y reconocer la nomenclatura es practicamente instantaneo
aunque haya cientos o miles de equipos (es una sola consulta a
PowerShell mas comparaciones de texto). Lo unico que podria ser lento es
el ping de verificacion, asi que TODOS los pings se lanzan en paralelo
(ver MAX_PINGS_CONCURRENTES), --sin-red sigue siendo la
opcion mas veloz.
========================================================================
