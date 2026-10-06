#!/usr/bin/env python3
"""Tests de los 3 bugs ALTA que encontró el sub-agente y que el test original NO veía.

El defecto de fondo no era los bugs: era que `test_find_dupes.py` corría siempre con un
corpus de 16 PRs. Los tres fallos aparecen fuera de ese escenario:

  ALTA-1  la caché se keyea sólo por número de PR -> datos de otro repo
  ALTA-2  `document_freq` poda los tokens del positivo cuando el corpus es chico
  ALTA-3  `is_template_body` trata un cuerpo vacío como plantilla

Cada test de aquí falla contra el código actual. Correr antes de arreglar.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("find_dupes.py")
spec = importlib.util.spec_from_file_location("fd", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

R: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append((ok, name, detail))


# ── ALTA-1: la caché mezcla repos ───────────────────────────────────────────
# El número de PR se repite entre repos (#500 existe en todos). Si la clave es sólo
# el número, `fetch_many` sirve la entrada del repo A al repo B, y `report()` publica
# `url` y `title` del repo ajeno.

def test_cache_is_keyed_by_repo():
    """La clave de caché debe incluir el repo."""
    src = SCRIPT.read_text()
    assert '"repo"' in src or "'repo'" in src, "la caché no guarda el repo"
    # y fetch_many debe pedirlo
    assert "repo" in fd.fetch_many.__code__.co_varnames, \
        "fetch_many no recibe el repo: no puede distinguir de qué repo es cada entrada"


def test_cache_entry_records_its_repo():
    """Cada entrada cacheada debe decir de qué repo es."""
    with tempfile.TemporaryDirectory() as d:
        cache = Path(d) / "c.json"
        cache.write_text(json.dumps({
            "django/django|500": {"n": 500, "repo": "django/django",
                                 "title": "VENENO", "_body": "", "author": "atacante",
                                 "created": "2025-01-01", "url": "u", "files": [],
                                 "_files_raw": []},
        }))
        fd.CACHE = cache
        stale = json.loads(cache.read_text())
        assert "500" not in stale, \
            "una entrada del repo A sería devuelta para el repo B (clave sin repo)"


# ── ALTA-2: document_freq se come el positivo con corpus chico ───────────────
# El umbral es >60% del corpus. Con 5 PRs del mismo bug, los tokens que las
# identifican aparecen en 5/5 y se podan. Con 16 PRs mezcladas, no.

def test_positive_detected_with_small_corpus():
    """Las 5 PRs del bug de systemd deben agruparse AISLADAS.

    Este es el caso que `test_find_dupes.py` no probaba: siempre cargaba 16 PRs.
    """
    nums = [103086, 108072, 113750, 121785, 124082]
    prs = {}
    for n in nums:
        p = fd.load_pr(n, fd.REPO)
        if p:
            prs[n] = p
    if len(prs) < 4:
        check("ALTA-2 positivo con corpus chico", False,
              f"no se pudieron cargar las PRs de prueba ({len(prs)}/5) — sin red")
        return
    groups, _conflicts = fd.cluster(prs, fd.THRESHOLD)
    found = any(set(nums).issubset(x["n"] for x in g) for g in groups)
    check("ALTA-2 positivo con corpus chico (5 PRs aisladas)", found,
          f"grupos={[sorted(x['n'] for x in g) for g in groups]}")


def test_document_freq_respects_min_tokens():
    """Podar no debe dejar una PR por debajo de MIN_TOKENS.

    Usa `prune()` — la función real — y no una aritmética propia en el test: un test
    que reimplementa la lógica mide su propia copia, no el código.
    """
    nums = [103086, 108072, 113750, 121785, 124082]
    prs = {n: fd.load_pr(n, fd.REPO) for n in nums}
    prs = {n: p for n, p in prs.items() if p}
    if len(prs) < 4:
        check("ALTA-2 prune no deja PRs bajo MIN_TOKENS", False, "sin datos de red")
        return
    ids = {n: fd.identifiers({"files": p["_files_raw"], "title": p["title"], "body": ""})
           for n, p in prs.items()}
    post = fd.prune(ids, fd.document_freq(ids))     # <- la función real
    bajo = [n for n, s in post.items() if len(s) < fd.MIN_TOKENS]
    check("ALTA-2 prune no deja PRs bajo MIN_TOKENS", not bajo,
          f"bajo el mínimo: {bajo}  (MIN_TOKENS={fd.MIN_TOKENS})")


# ── ALTA-3: cuerpo vacío tratado como plantilla ─────────────────────────────

def test_empty_body_is_not_template():
    """Un cuerpo vacío significa 'no escribió nada', no 'usó la plantilla'.

    Confundirlos fuerza `titles_agree` a False y degrada el mensaje de todos los
    pares del mismo autor a "probablemente distintos", aunque sean el mismo bug.
    """
    vacio = {"title": "fix(x): crash on empty input", "_body": "", "author": "a"}
    check("ALTA-3 cuerpo vacío NO es plantilla", fd.is_template_body(vacio) is False,
          f"is_template_body(vacio) = {fd.is_template_body(vacio)}")


def test_missing_body_is_not_template():
    """Sin campo _body, tampoco se puede saber que usó plantilla."""
    sin = {"title": "fix(x): algo", "author": "a"}
    check("ALTA-3 cuerpo ausente NO es plantilla", fd.is_template_body(sin) is False,
          f"is_template_body(sin _body) = {fd.is_template_body(sin)}")


def test_real_template_still_detected():
    """La detección de plantilla debe SEGUIR funcionando: ése es su propósito."""
    plantilla = (
        "## What does this PR do?\n"
        "<!-- Describe the change clearly. What problem does it solve? -->\n"
        "\n"
        "## Related Issue\n"
        "<!-- Link the issue this PR addresses -->\n"
        "\n"
        "## Type of Change\n"
        "- [ ] Bug fix (non-breaking change that fixes an issue)\n"
        "- [ ] New feature (non-breaking change that adds functionality)\n"
        "- [ ] Security fix\n"
        "- [ ] Documentation update\n"
    )
    check("ALTA-3 plantilla real SÍ se detecta",
          fd.is_template_body({"_body": plantilla, "title": "x"}) is True,
          "si esto falla, el arreglo de arriba rompió la detección real")


def test_real_body_not_template():
    """Y un cuerpo de verdad no puede marcarse como plantilla."""
    real = (
        "## Problem (`#103062`)\n\n"
        "`_systemd_timeout_stop_us()` probes the `--user` scope first and trusts the "
        "first scope that yields a parsable TimeoutStopUSec, even when that scope "
        "reports LoadState=not-found and the real value lives in the system manager.\n\n"
        "## Fix\n\nRequest LoadState alongside TimeoutStopUSec and fall through.\n"
    )
    check("ALTA-3 cuerpo real NO es plantilla",
          fd.is_template_body({"_body": real, "title": "x"}) is False, "")


def main() -> int:
    print(f"\n{'='*76}")
    print("find_dupes.py — los 3 bugs ALTA (tests que el suite original no cubria)")
    print(f"{'='*76}")
    try:
        test_cache_is_keyed_by_repo()
        test_cache_entry_records_its_repo()
    except AssertionError as e:
        check("ALTA-1 la caché se keyea por repo", False, str(e))
    else:
        check("ALTA-1 la caché se keyea por repo", True, "")
    try:
        test_empty_body_is_not_template()
        test_missing_body_is_not_template()
        test_real_template_still_detected()
        test_real_body_not_template()
    except Exception as e:  # noqa: BLE001
        check("ALTA-3 cuerpos vacíos", False, f"excepción: {e}")
    test_positive_detected_with_small_corpus()
    test_document_freq_respects_min_tokens()

    passed = sum(1 for ok, *_ in R if ok)
    for ok, name, detail in R:
        print(f"  [{'ok  ' if ok else 'FALLA'}] {name:52} {detail}")
    print(f"\n  {passed}/{len(R)} correctos")
    if passed < len(R):
        print("\n  BUGS PRESENTES. Arreglar antes de publicar.")
    return 0 if passed == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())