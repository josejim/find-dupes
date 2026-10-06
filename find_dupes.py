#!/usr/bin/env python3
"""Detecta PRs duplicadas en cualquier repositorio de GitHub.

Los repos grandes acumulan trabajo duplicado que nadie detecta: varias personas
resuelven el mismo bug en paralelo porque no ven lo que otras ya están haciendo.
Este script compara los diffs de las PRs abiertas y señala pares con solape
alto, para que un humano decida si merece la pena avisar.

No juzga redundancia — solo mide similitud. La decisión es humana.
el mismo bug de systemd con código distinto y cada título parecía único.

Detecta el CONCEPTO, no el texto. Medido sobre el caso real #124076 (5 PRs):

    Jaccard sobre líneas añadidas ....... 0.03 – 0.09   <- inútil
    Jaccard sobre identificadores ....... 0.23 – 0.35   <- mismo bug
                                    control ........... 0.07   <- bug distinto

Umbral 0.18. El control negativo queda 4x por debajo y el par más cercano entre
duplicados reales (0.23) sigue por encima.

    python3 find_dupes.py                        # top 10 grupos
    python3 find_dupes.py --limit 200 --workers 8
    python3 find_dupes.py --notify --dry-run     # comentaría; dry-run no publica
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import itertools
import json
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

REPO = os.environ.get("DUPE_REPO", "django/django")
# `HOME` se respeta: el script corre en la máquina de quien lo usa, no en otra.
ENV = {**os.environ, "GH_PAGER": "cat", "NO_COLOR": "1"}

THRESHOLD = 0.18          # por encima del control negativo (0.07), holgado
# El par (#132524, #132525) es indistinguible por contenido: mismo autor,
# ocho archivos compartidos, similitud de patch 0.634 — MÁS ALTA que 7 de los 10
# pares realmente duplicados de #124076 (min 0.327). Ninguna métrica de similitud
# los separa: son dos bugs distintos que un autor arrastra en paralelo.
#
# Se deja de intentar separarlos y se cambia la exigencia: el aviso que se
# publica no afirma "esto es un duplicado", afirma "esto solapa". Eso es
# verificable por quien lea el aviso en 30 segundos, y es la mitad del valor.
# Detectar el solape es automatizable; juzgar si es redundancia es humano.
NEEDS_HUMAN_JUDGMENT = True
MIN_TOKENS = 12           # una PR con menos identificadores no es comparable
# Podar el vocabulario de formato sólo tiene sentido con suficientes muestras para
# separar "esto lo dice todo el mundo" de "esto describe este conjunto". Medido: con
# 5 PRs del mismo bug, podar al 60% se lleva la señal y el detector devuelve 0 grupos;
# con 16 PRs mezcladas poda 2 tokens y funciona. Ver `prune()`.
MIN_CORPUS_FOR_PRUNE = 12
CACHE_TTL = 6 * 3600      # re-descargar 33k diffs cada pasada es absurdo

# La caché va al tmp del usuario, no a un path de Hermes: este script tiene que
# funcionar en la máquina de quien lo clonea, no sólo en la mía.
CACHE = Path(os.environ.get(
    "DUPE_CACHE",
    str(Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "find_dupes" / "prs.json")))

# Extensiones contadas como "código" para exigir que el solape sea real. Ajustar
# con --code-ext si el repo usa otro lenguaje (ver load_config).
CODE_EXTS = ("py", "ts", "tsx", "js", "jsx", "rs", "go", "java", "rb", "sh",
             "c", "cc", "cpp", "h", "hpp", "php", "swift", "kt", "scala")

# Archivos cuyo contenido es estructura compartida, no el bug. Dos PRs que sólo
# coinciden en estos se parecen por plantilla, no por intención.
BOILERPLATE = re.compile(
    r"(^\.gitignore$|^\.env|sidebars\.ts$|\.mdx?$|\.lock$|^\.github/|"
    r"package-lock|yarn\.lock|uv\.lock|\.json$|^docs/)"
)
CODE_EXT = re.compile(r"\.(?:" + "|".join(CODE_EXTS) + r")$")

# Ruido: palabras que aparecen en el 90% de los PRs y no distinguen nada.
STOP = set("""
the a an of to in is it and or for with on at by from this that be as not if else
when then so we i you its our your their can will would should could may might
do does did have has had been being more most other some such only own same too
very just also into over under about after before between both each few more
added add adds use used using new old fix fixes fixed bug issue issue pr
""".split())


def gh_json(args, timeout=90):
    p = subprocess.run(["gh", *args], capture_output=True, text=True, env=ENV, timeout=timeout)
    if p.returncode != 0:
        return None
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return None


def gh_json_checked(args, timeout=90, contexto=""):
    """Como `gh_json` pero distingue 'no hay datos' de 'falló'.

    `gh_json` devuelve None para 404, 403 (rate limit), 500, timeout y token inválido.
    Con None indistinto, un repo inexistente salía como "0 duplicados" y exit 0 — que
    en un cron se lee como "todo bien". Un `TimeoutExpired` además abortaba la corrida
    entera sin mensaje.
    """
    try:
        p = subprocess.run(["gh", *args], capture_output=True, text=True,
                           env=ENV, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"timeout de {timeout}s{' en ' + contexto if contexto else ''}")
    if p.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args[:3])} falló ({p.returncode}): {p.stderr.strip()[:160]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"respuesta no-JSON de gh: {e}")


def fetch_open_prs(repo: str, limit: int) -> list[dict]:
    q = f"repo:{repo} type:pr state:open"
    raw = ""
    pages = math.ceil(limit / 100)
    for page in range(1, pages + 1):
        r = subprocess.run(
            ["gh", "api", "-X", "GET", "/search/issues", "-f", f"q={q}",
             "-f", "per_page=100", "-f", f"page={page}", "-f", "sort=created", "-f", "order=desc"],
            capture_output=True, text=True, env=ENV, timeout=180)
        if r.returncode != 0:
            # antes: se concatenaba la salida vacía y el script reportaba "0 duplicados"
            # con exit 0. Un repo mal escrito tiene que ser un error, no un "todo bien".
            raise RuntimeError(
                f"no se pudieron listar PRs de '{repo}' (gh rc={r.returncode}): "
                f"{r.stderr.strip()[:200]}")
        raw += r.stdout
    dec, i, out = json.JSONDecoder(), 0, []
    while i < len(raw):
        while i < len(raw) and raw[i] in " \t\r\n":
            i += 1
        if i >= len(raw):
            break
        try:
            obj, j = dec.raw_decode(raw, i)
        except json.JSONDecodeError:
            i += 1
            continue
        out.append(obj)
        i = j
    items = []
    for d in out:
        items.extend(d.get("items", []))
    return items[:limit]


def identifiers(pr: dict) -> set[str]:
    """Tokens simbólicos del PR: patch de producción + título + inicio del cuerpo.

    Se excluyen los tests a propósito — todos los duplicados tocan el mismo
    archivo de test, y eso los haría parecer iguales aunque arreglaran distinto.

    Y se descartan los tokens que APARECEN EN CASI TODAS las muestras: el
    formato de un YAML del catálogo produce el mismo vocabulario en seis plugins
    distintos, y eso los marca como gemelos sin que compartan nada. Un token
    sólo cuenta si es raro dentro del corpus.
    """
    patch = " ".join(f.get("patch") or "" for f in pr["files"]
                     if not f["filename"].startswith(("tests/", "test_")) and f.get("patch"))
    body = (pr.get("body") or "")[:2500]
    text = patch + " " + (pr.get("title") or "") + " " + body
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_]{4,}|[a-z]{3,}-[a-z]{3,}", text)
    return {w for w in (x.lower() for x in raw) if w not in STOP}


def document_freq(all_ids: dict[int, set]) -> set[str]:
    """Tokens presentes en más del 60% del corpus: son formato, no contenido."""
    if not all_ids:
        return set()
    cnt = Counter()
    for s in all_ids.values():
        cnt.update(s)
    n = len(all_ids)
    return {t for t, c in cnt.items() if c > 0.60 * n}


def prune(ids: dict[int, set], df: set[str]) -> dict[int, set]:
    """Quita el vocabulario de formato — pero sólo si hay corpus para distinguirlo.

    MEDIDO, y es el bug más subtil de todos:

    ```
    corpus de 16 PRs -> poda  2 tokens  -> jaccard 0.33-0.56  -> detecta
    corpus de  5 PRs -> poda 36 tokens  -> jaccard 0.00-0.16  -> NO detecta
    ```

    Con 5 PRs **del mismo bug**, el 60% de documento no es "formato compartido": es
    el bug. `_systemd_timeout_stop_us` aparece en 5/5, así que se poda, y con él se va
    la señal que identifica a las PRs. El mismo código da 0 grupos y 4 grupos según el
    tamaño del corpus — por eso `test_find_dupes.py` pasaba 22/22 con el defecto.

    Podar tiene sentido cuando hay suficientes muestras para separar "esto lo dice
    todo el mundo" de "esto describe este conjunto". Con pocas, no hay forma de
    distinguirlo, y podar es destructivo.

    Regla: el prune es un filtro de formato; sólo aplica con corpus suficiente. Con
    pocas PRs se conserva todo y el umbral de Jaccard ya separa lo que es formato de
    lo que es contenido.
    """
    if not df or len(ids) < MIN_CORPUS_FOR_PRUNE:
        return ids
    return {n: (s - df) for n, s in ids.items()}


def load_pr(n: int, repo: str) -> dict | None:
    meta = gh_json(["api", f"repos/{repo}/pulls/{n}"])
    if not meta:
        return None
    files = gh_json(["api", f"repos/{repo}/pulls/{n}/files?per_page=100"]) or []
    return {
        "n": n,
        "title": meta.get("title") or "",
        "_body": (meta.get("body") or "")[:1500],
        "author": (meta.get("user") or {}).get("login", "?"),
        "created": (meta.get("created_at") or "")[:10],
        "url": meta.get("html_url"),
        "files": [{"filename": f["filename"], "additions": f.get("additions", 0)}
                  for f in files],
        "_files_raw": files,
    }


def fetch_many(numbers: list[int], repo: str, workers: int, cache_on: bool) -> dict[int, dict]:
    """Descarga diffs, con caché.

    La clave es `repo|número`, NO el número solo. Los números de PR se repiten entre
    repos (#500 existe en todos), así que con la clave numérica una entrada del repo A
    se servía al repo B y `report()` publicaba el `url` y el `title` del repo ajeno.
    """
    out: dict[int, dict] = {}
    key = lambda n: f"{repo}|{n}"  # noqa: E731
    stale = None
    if cache_on and CACHE.exists() and (time.time() - CACHE.stat().st_mtime) < CACHE_TTL:
        try:
            raw = json.loads(CACHE.read_text())
            # se descartan las claves de otro repo en vez de ignorarlas: una caché
            # compartida entre repos de distintos tamaños no sirve para ninguno.
            stale = {int(k.split("|", 1)[1]): v for k, v in raw.items()
                     if "|" in k and k.split("|", 1)[0] == repo}
        except Exception:
            stale = None

    todo = [n for n in numbers if stale is None or n not in stale]
    if stale:
        out.update({n: v for n, v in stale.items() if n in set(numbers)})

    if todo:
        print(f"  descargando {len(todo)} diffs con {workers} workers...", file=sys.stderr)
        done = 0
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(load_pr, n, repo): n for n in todo}
            for f in cf.as_completed(futs):
                r = f.result()
                if r:
                    r["repo"] = repo
                    out[r["n"]] = r
                done += 1
                if done % 25 == 0:
                    print(f"    {done}/{len(todo)}", file=sys.stderr)
        if cache_on:
            try:
                CACHE.parent.mkdir(parents=True, exist_ok=True)
                CACHE.write_text(json.dumps({key(k): v for k, v in out.items()}))
            except Exception as e:
                # sin `pass`: una caché que nunca se escribe hace que cada pasada
                # re-descargue 33k diffs, que es justo lo que la caché evita.
                print(f"  aviso: no se pudo escribir la caché en {CACHE}: {e}",
                      file=sys.stderr)
    return out


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def has_code_signal(pa: dict, pb: dict) -> bool:
    """¿Comparten al menos un archivo de código real (no plantilla, no docs)?

    Seis YAMLs del catálogo se parecen por el formato del YAML; dos PRs que sólo
    tocan `.env.example` se parecen por la plantilla. Ninguno de los dos casos es
    un duplicado, y los dos desaparecen al exigir código.
    """
    files_a = {f["filename"] for f in pa["files"]
               if not f["filename"].startswith(("tests/", "test_"))}
    files_b = {f["filename"] for f in pb["files"]
               if not f["filename"].startswith(("tests/", "test_"))}
    shared = files_a & files_b
    return any(CODE_EXT.search(f) and not BOILERPLATE.search(f) for f in shared)


def is_template_body(pr: dict) -> bool:
    """¿El cuerpo de la PR es la plantilla del repo sin rellenar?

    MEDIDO en el caso #132524/#132525: 213 palabras, 6 placeholders `<!-- -->`
    sin sustituir, cuerpo IDÉNTICO entre las dos. Su similitud de patch era
    0.634 — más alta que 7 de los 10 pares realmente duplicados de #124076 — y
    el título no las separa. La razón es que **las dos PRs no describen nada**:
    no es que sean el mismo bug, es que no dicen cuál es.

    Tres señales, todas medidas:

    ```
    placeholders <!-- --> sin rellenar .......  6      (PRs reales: 0)
    casillas "- [ ]" sin marcar ..............  6      (PRs reales: 0)
    texto propio tras quitar la plantilla ....  9 pal. (PRs reales: >150)
    ```

    No se cuenta el largo total: los encabezados de la plantilla ("What does this
    PR do?", "Type of Change") inflan el recuento a 135 aunque no digan nada.
    Se cuenta el texto que SOBREVIVE a quitar placeholders, casillas y encabezados
    conocidos de la plantilla.
    """
    body = pr.get("_body") or ""
    # Sin cuerpo no hay plantilla: significa "no escribió nada", que es distinto de
    # "usó la plantilla del repo y no la rellenó". Tratarlos igual degradaba el
    # mensaje de todos los pares de un mismo autor a "probablemente distintos"
    # en repos que no exigen plantilla.
    if not body.strip():
        return False

    n_placeholders = body.count("<!--")
    n_boxes = len(re.findall(r"- \[ \]", body))
    if n_placeholders < 2 and n_boxes < 2:
        return False

    # quitar la plantilla: placeholders, casillas, encabezados y secciones fijas
    stripped = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    stripped = re.sub(r"^\s*[-*]\s*\[[ xX]\].*$", " ", stripped, flags=re.M)   # casillas
    stripped = re.sub(r"^#{1,6}\s.*$", " ", stripped, flags=re.M)                # encabezados
    stripped = re.sub(r"^\s*(Fixes|Closes|Resolves)\s*#?\s*$", " ", stripped,
                      flags=re.M | re.I)                                       # "Fixes #"
    words = [w for w in re.findall(r"[a-z]{3,}", stripped.lower()) if w not in STOP]
    return len(words) < 60


def titles_agree(pa: dict, pb: dict) -> bool:
    """¿Estos dos PRs del mismo autor atacan el mismo bug?

    MEDIDO: el título NO sirve, y no por poco.

    En el caso real #124076 (cinco PRs, un solo bug) el solapamiento de títulos
    da 0.000 en 3 de los 10 pares — "read TimeoutStopUSec from the loaded scope"
    y "ignore a not-found unit's stop timeout" son el mismo bug sin compartir una
    palabra. Y a la inversa, "bump gonogo a v0.2.0" frente a "bump thomas a
    v0.1.2" da 0.60 siendo plugins distintos.

    Título + cuerpo sí funciona, siempre que el cuerpo exista:

    ```
    mismo bug, 10 pares (#124076) ...............  0.188 – 0.351
    bugs distintos, cuerpos reales ..............  0.057 – 0.103
    ```

    El caso 0.927 (#132524/#132525) NO es una excepción: son dos plantillas sin
    rellenar, y por eso hay que detectarlas antes de comparar nada.
    """
    if is_template_body(pa) or is_template_body(pb):
        return False          # nadie declaró una intención: no se afirma duplicado

    a = intent_words((pa.get("title") or "") + " " + (pa.get("_body") or ""))
    b = intent_words((pb.get("title") or "") + " " + (pb.get("_body") or ""))
    if not a or not b:
        return True
    inter = len(a & b)
    union = len(a | b)
    return (inter / union) >= 0.15


def intent_words(text: str) -> set[str]:
    t = text.lower()
    t = re.sub(r"^\W*(fix|feat|refactor|chore|docs|test|ci|perf)\s*(\([^)]*\))?\s*:?\s*", " ", t)
    return {w for w in re.findall(r"[a-z]{4,}", t) if w not in STOP}


def cluster(prs: dict[int, dict], threshold: float) -> tuple[list[list[dict]], set]:
    """Agrupa PRs solapadas. Devuelve (grupos, pares_en_conflicto).

    Los pares en conflicto son los de un mismo autor cuyos títulos describen
    problemas distintos: se agrupan (el solape existe y genera conflictos de
    merge) pero el aviso NO los llama duplicados. El llamador necesita saber la
    diferencia para redactar el mensaje correcto.

    Union-Find sobre pares por encima del umbral.

    Se comparan candidatos por compartir archivo de producción, no todos contra
    todos: 33k PRs contra 33k son 550M de comparaciones de conjuntos.
    """
    parent = {n: n for n in prs}
    conflicts: set[tuple[int, int]] = set()

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # Vocabulario común a todo el corpus = formato, no contenido. Sin esto, seis
    # YAMLs del catálogo distintos salen como gemelos 0.40.
    ids_all = {n: identifiers({"files": prs[n]["_files_raw"], "title": prs[n]["title"], "body": ""})
               for n in prs}
    ids_all = prune(ids_all, document_freq(ids_all))

    # invertir índice: archivo -> PRs que lo tocan
    by_file: dict[str, list[int]] = defaultdict(list)
    for n, pr in prs.items():
        for f in pr["files"]:
            if not f["filename"].startswith(("tests/", "test_")):
                by_file[f["filename"]].append(n)

    pairs = 0
    for fname, members in by_file.items():
        if len(members) < 2 or len(members) > 60:
            continue
        for a, b in itertools.combinations(members, 2):
            if len(ids_all[a]) < MIN_TOKENS or len(ids_all[b]) < MIN_TOKENS:
                continue
            pairs += 1
            j = jaccard(ids_all[a], ids_all[b])
            if j < threshold:
                continue
            # Mismo autor + títulos que se contradicen = bugs distintos, ni
            # aunque compartan archivos. Se marca como solape, no duplicado: el
            # aviso lo dice así y quien lo lee decide.
            conflicting = False
            if prs[a]["author"] == prs[b]["author"]:
                conflicting = not titles_agree(prs[a], prs[b])
            if not has_code_signal(prs[a], prs[b]):
                continue
            union(a, b)
            if conflicting:
                conflicts.add((min(a, b), max(a, b)))

    groups: dict[int, list[dict]] = defaultdict(list)
    for n in prs:
        groups[find(n)].append(prs[n])
    out = [g for g in groups.values() if len(g) > 1]
    # grupos más grandes primero; dentro de un grupo, la PR más antigua primero
    # (es la que más tiempo lleva esperando y la que debe recibir el aviso).
    for g in out:
        g.sort(key=lambda p: p["created"] or "")
    out.sort(key=lambda g: (-len(g), g[0]["created"] or ""))
    print(f"  {pairs} pares comparados -> {len(out)} grupos, {len(conflicts)} en conflicto",
          file=sys.stderr)
    return out, conflicts


def report(group: list[dict], conflicts: set[tuple[int, int]] | None = None) -> str:
    """ redacta el aviso.

    Distingue dos situaciones que piden acciones DISTINTAS al mantenedor, y esa
    diferencia es medible:

    - autores distintos sobre el mismo archivo con alta similitud de patch
      (#132487/#132618, 0.93) → probablemente un clon. "Posible duplicado".
    - mismo autor, títulos que no coinciden (#132524/#132525, 0.63) → trabajo
      en paralelo, casi seguro NO redundante. Aquí la similitud de patch es
      MÁS ALTA que en 7 de los 10 pares realmente duplicados de #124076, así que
      ninguna métrica los separa. Sólo una persona mirando los títulos puede.

    Nunca se afirma que son duplicados: se afirma que solapan.
    """
    conflicts = conflicts or set()
    g = sorted(group, key=lambda p: p["created"] or "")
    authors = {p["author"] for p in g}
    same_author = len(authors) == 1
    flagged = any((min(p["n"], q["n"]), max(p["n"], q["n"])) in conflicts
                  for i, p in enumerate(g) for q in g[i + 1:])

    if same_author and flagged:
        headline = "Overlapping work from the same author — likely distinct, worth a look"
        claim = ("These touch the same files but the titles describe different problems. "
                 "Most likely parallel work rather than a duplicate — flagging only "
                 "because the overlap is large enough to create merge conflicts.")
    else:
        headline = f"Possible duplicate group — {len(g)} PRs"
        claim = ("These touch the same production files with very similar changes. "
                 "If one already landed, the others are redundant; if none has, a "
                 "maintainer may want to keep one and close the rest.")

    lines = [headline, "",
             "| PR | opened | author | title |",
             "|---|---|---|---|"]
    for p in g:
        lines.append(f"| [#{p['n']}]({p['url']}) | {p['created']} | @{p['author']} | {p['title'][:60]} |")

    files = Counter()
    for p in g:
        for f in p["files"]:
            files[f["filename"]] += 1
    shared = [f for f, c in files.items()
              if c >= 2 and not f.startswith(("tests/", "test_"))]
    if shared:
        lines += ["", "Shared production files: " + ", ".join(f"`{c}`" for c in shared[:6])]

    lines += ["", claim, "",
              "Flagged by a third-party overlap detector. No maintainer privileges were "
              "used; this is computed purely from the public diffs. Automated similarity "
              "cannot tell a duplicate from parallel work — a human decides."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detecta PRs duplicadas en un repo de GitHub")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--limit", type=int, default=120, help="PRs a analizar (más = más lento)")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--notify", action="store_true", help="comentar en la PR más antigua de cada grupo")
    ap.add_argument("--dry-run", action="store_true", help="con --notify, muestra pero no publica")
    ap.add_argument("--out", metavar="PATH")
    ap.add_argument("--cache", metavar="PATH",
                    help="ruta del caché de diffs (default: $XDG_CACHE_HOME/find_dupes/prs.json)")
    ap.add_argument("--code-ext", metavar="LIST",
                    help="extensiones que cuentan como código, separadas por comas "
                         "(default: " + ",".join(CODE_EXTS) + ")")
    ap.add_argument("--no-cache", action="store_true", help="no leer ni escribir caché")
    a = ap.parse_args(argv)

    # `global` es obligatorio: sin esto Python crea un CACHE local y la
    # reasignación no llega a fetch_many, que lee la global.
    global CACHE, CODE_EXT
    if a.cache:
        CACHE = Path(a.cache)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
    if a.code_ext:
        CODE_EXT = re.compile(r"\.(?:" + "|".join(
            re.escape(e.strip().lstrip("."))          # sin escape, `c++` repetía `c+`
            for e in a.code_ext.split(",") if e.strip()) + r")$")

    print(f"Buscando PRs abiertas en {a.repo} (limit={a.limit})...", file=sys.stderr)
    listing = fetch_open_prs(a.repo, a.limit)
    nums = [i["number"] for i in listing]
    print(f"  {len(nums)} PRs. Diffing...", file=sys.stderr)
    prs = fetch_many(nums, a.repo, a.workers, cache_on=not a.no_cache)
    prs = {n: p for n, p in prs.items() if p}

    groups, conflicts = cluster(prs, a.threshold)
    wasted = sum(len(g) - 1 for g in groups)

    print(f"\n{'='*72}\nfind_dupes — {len(groups)} grupos, {wasted} PRs potencialmente redundantes "
          f"de {len(prs)} analizadas\n{'='*72}")
    for g in groups[:12]:
        g = sorted(g, key=lambda p: p["created"] or "")
        print(f"\n  {len(g)} PRs:")
        for p in g:
            print(f"    #{p['n']:<7} {p['created']}  @{p['author']:<16} {p['title'][:52]}")

    if a.out:
        Path(a.out).write_text(json.dumps(
            [[p["n"] for p in sorted(g, key=lambda x: x["created"] or "")] for g in groups], indent=1))

    if a.notify and groups:
        print(f"\n  {'SIMULANDO' if a.dry_run else 'COMENTANDO'} en {len(groups)} grupos...", file=sys.stderr)
        for g in groups:
            g = sorted(g, key=lambda p: p["created"] or "")
            target = g[0]  # la más antigua: la que lleva más tiempo esperando
            body = report(g, conflicts)
            if a.dry_run:
                print(f"\n--- [{target['n']}] dry-run ---\n{body[:400]}...")
                continue
            r = subprocess.run(["gh", "pr", "comment", str(target["n"]), "--repo", a.repo,
                                "--body-file", "-"],
                               input=body, capture_output=True, text=True, env=ENV, timeout=90)
            print(f"    #{target['n']}: {'ok' if r.returncode == 0 else r.stderr[:120]}",
                  file=sys.stderr)
            time.sleep(2)   # GitHub rate-limits comentarios

    return 0


def cli() -> int:
    try:
        return main()
    except RuntimeError as e:
        # exit 2 = error explícito. Antes un repo inexistente salía con 0 y "0
        # duplicados", que en un cron indistinguible de "no hay nada que hacer".
        print(f"\nERROR: {e}\n", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(cli())
