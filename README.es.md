# find-dupes

**Detecta PRs solapadas (posiblemente duplicadas) en cualquier repositorio de GitHub.**

Los repos grandes acumulan trabajo duplicado que nadie detecta: varias personas
resuelven el mismo bug en paralelo porque no ven lo que otras ya están haciendo.
Este script compara los diffs de las PRs abiertas y señala pares con solape
alto, para que un humano decida si merece la pena avisar.

**No juzga redundancia — solo mide similitud. La decisión es humana.**

## Requisitos

- Python 3.10+
- [GitHub CLI](https://cli.github.com/) (`gh`) autenticado

Sin dependencias de Python. Solo stdlib.

## Instalación

```bash
git clone https://github.com/josejim/find-dupes.git
cd find-dupes
chmod +x find_dupes.py
```

## Uso

```bash
# Analizar las 120 PRs abiertas más recientes de un repo
./find_dupes.py --repo django/django

# Más PRs, más workers
./find_dupes.py --repo psf/requests --limit 300 --workers 12

# Umbral más estricto (más similitud requerida)
./find_dupes.py --repo pallets/flask --threshold 0.25

# Solo ciertos tipos de archivo cuentan como código (ignora docs, tests, CI)
./find_dupes.py --repo python/cpython --code-ext py

# Sin caché (siempre descarga de nuevo)
./find_dupes.py --repo rust-lang/rust --no-cache

# Ver qué se comentaría, sin publicar nada
./find_dupes.py --repo owner/name --comment --dry-run
```

### Opciones

| Flag | Default | Descripción |
|---|---|---|
| `--repo` | `django/django` | Repositorio `owner/name` |
| `--limit` | `120` | PRs abiertas más recientes a analizar (más = más lento) |
| `--threshold` | `0.18` | Similitud mínima de identificadores (0–1) para marcar un par |
| `--workers` | `6` | Descargas de diffs en paralelo |
| `--cache` / `--no-cache` | caché on | Caché de diffs en `~/.cache/find_dupes/prs.json` (respeta `$XDG_CACHE_HOME`; TTL 6 h) |
| `--code-ext` | `py,ts,tsx,js,jsx,rs,go,java,rb,sh,c,cc,cpp,h,hpp,php,swift,kt,scala` | Extensiones que cuentan como código |
| `--out` | — | Escribir los números de grupo detectados como JSON en un archivo |
| `--comment` (alias: `--notify`) | off | Comentar en la PR más antigua de cada grupo |
| `--dry-run` | off | Con `--comment`, imprimir los comentarios en vez de publicarlos |
| `--version` | — | Mostrar versión |

### ⚠️ La guardarraíl de `--comment`

Los comentarios son acciones públicas sobre PRs de otras personas. Corre primero
`--comment --dry-run`, lee la salida y solo entonces publica. **Nunca corras
`--comment` sin supervisión desde automatización.** La herramienta nunca afirma
"esto es un duplicado" — afirma "esto solapa", que cualquiera puede verificar
en 30 segundos leyendo las PRs.

## Cómo funciona

1. **Descarga** los diffs de las N PRs abiertas más recientes vía `gh api`.
2. **Extrae identificadores** de cada diff: nombres de funciones, variables,
   constantes, rutas de archivos. Ignora formato (comentarios, imports, boilerplate).
3. **Calcula Jaccard** entre pares de PRs que comparten al menos un archivo
   de código.
4. **Filtra** pares con similitud por debajo del umbral.
5. **Agrupa** pares conectados (Union-Find) y reporta clusters.

### Por qué Jaccard sobre identificadores y no texto

Dos PRs pueden resolver el mismo bug con código completamente distinto pero
mismas funciones, variables y rutas. Jaccard sobre identificadores captura
el **concepto**, no la sintaxis.

### Por qué no juzga redundancia

Un par con 80 % de similitud puede ser:

- un duplicado real (dos personas, mismo enfoque)
- trabajo complementario (una refactoriza, otra añade una feature)
- coincidencia (mismo dominio, distinto problema)

El script señala. El humano decide.

## Caso de estudio: SmartTube (octubre 2026)

Corrido sobre la cola de PRs abiertas de [SmartTube](https://github.com/yuliskov/SmartTube),
un cliente de media para Android TV con mucho movimiento:

```
50 PRs abiertas analizadas  ->  5 grupos de solape, 11 PRs  (~22 % de la cola)
```

Qué encontraron los grupos:

- dos implementaciones de voice-over (#5817, #6265)
- dos reescrituras de subtítulos (#5839, #6272)
- tres tomas del mismo playback de playlists (#5790, #5791, #6162)
- dos arreglos paralelos de errores del player (#6122, #6188) — un tercero (#6295)
  fue aplicado a mano por el mantenedor después

8 de las 11 PRs están marcadas `CONFLICTING` en GitHub: los conflictos de merge
mutuos son el síntoma visible. Los solapes no se ven en la lista de PRs — de eso
se trata.

## Tests

```bash
python3 test_find_dupes.py      # control: conjuntos de PRs conocidos (positivo/negativo)
python3 test_regressions.py     # regresiones de bugs ya corregidos
python3 test_v02.py             # contrato de CLI v0.2 (sin red)
```

Las suites de control y regresión requieren `gh` autenticado y acceso a red; el
repo bajo prueba por defecto es `django/django` y se puede cambiar con
`DUPE_REPO=owner/name`.

## Casos de uso

- **Mantenedores**: detectar duplicados antes de revisar
- **Contribuidores**: evitar trabajar en algo que otro ya está haciendo
- **Investigadores**: medir la tasa de duplicación de un repo

## Contribuir

Issues y PRs bienvenidos. Los tests deben pasar antes de mergear.

## Licencia

MIT. Ver [LICENSE](LICENSE).
