# find-dupes

Detecta PRs duplicadas en cualquier repositorio de GitHub.

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
git clone https://github.com/tu-usuario/find-dupes.git
cd find-dupes
chmod +x find_dupes.py
```

## Uso

```bash
# Analizar las 120 PRs más recientes de un repo
./find_dupes.py --repo django/django

# Más PRs, más workers
./find_dupes.py --repo psf/requests --limit 300 --workers 12

# Umbral más estricto (más similitud requerida)
./find_dupes.py --repo pallets/flask --threshold 0.25

# Solo archivos de código (ignora docs, tests, CI)
./find_dupes.py --repo python/cpython --code-ext py

# Sin caché (siempre descarga de nuevo)
./find_dupes.py --repo rust-lang/rust --no-cache
```

### Opciones

| Flag | Default | Descripción |
|---|---|---|
| `--repo` | `django/django` | Repositorio `owner/name` |
| `--limit` | `120` | PRs a analizar (más = más lento) |
| `--threshold` | `0.18` | Similitud mínima (0–1) |
| `--workers` | `10` | Descargas en paralelo |
| `--cache` / `--no-cache` | caché on | Caché en `~/.cache/find-dupes/prs.json` |
| `--code-ext` | `py,ts,tsx,js,jsx,rs,go,java,rb,sh,c,cc,cpp,h,hpp,php,swift,kt,scala` | Extensiones de código a comparar |
| `--comment` | off | Publicar comentario en las PRs detectadas (requiere confirmación) |

## Cómo funciona

1. **Descarga** los diffs de las N PRs más recientes vía `gh api`.
2. **Extrae identificadores** de cada diff: nombres de funciones, variables,
   constantes, rutas de archivos. Ignora formato (comentarios, imports, boilerplate).
3. **Calcula Jaccard** entre pares de PRs que comparten al menos un archivo
   de código.
4. **Filtra** pares con similitud por debajo del umbral.
5. **Agrupa** pares conectados (Union-Find) y reporta clusters.

### Por qué Jaccard y no texto

Dos PRs pueden resolver el mismo bug con código completamente distinto pero
mismas funciones, variables y rutas. Jaccard sobre identificadores captura
el **concepto**, no la sintaxis.

### Por qué no juzga redundancia

Un par con 80% de similitud puede ser:
- Duplicado real (dos personas, mismo enfoque)
- Trabajo complementario (una refactoriza, otra añade feature)
- Coincidencia (mismo dominio, distinto problema)

El script señala. El humano decide.

## Tests

```bash
python3 test_find_dupes.py      # 22 tests de control
python3 test_regressions.py     # 7 tests de regresión (bugs ALTA)
```

Los tests requieren `gh` autenticado y acceso a los repos de prueba.

## Casos de uso

- **Mantenedores**: detectar duplicados antes de revisar
- **Contribuidores**: evitar trabajar en algo que otro ya está haciendo
- **Investigadores**: medir la tasa de duplicación en un repo

## Contribuir

Issues y PRs bienvenidos. Los tests deben pasar antes de mergear.

## Licencia

MIT. Ver [LICENSE](LICENSE).
