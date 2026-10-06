"""Control de find_dupes.py.

Dos casos, y los dos importan:

POSITIVO  — las 5 PRs de #124076: mismo bug, cinco implementaciones, títulos
            distintos. El detector DEBE agruparlas.

NEGATIVO  — #132529/#132530/#132571/#132572/#132573: seis YAMLs del catálogo
            de plugins, cada uno un plugin DISTINTO. Salen con Jaccard 0.40
            porque el formato del YAML produce el mismo vocabulario. El detector
            NO DEBE agruparlas.

El negativo importa más que el positivo: un detector que sólo sabe decir "sí"
marca la mitad del backlog como duplicada, y entonces nadie lo lee.
"""
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("find_dupes.py")
spec = importlib.util.spec_from_file_location("fd", SCRIPT)
fd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fd)

POSITIVE = [103086, 108072, 113750, 121785, 124082]   # mismo bug de systemd
NEGATIVE = [132529, 132530, 132571, 132572, 132573]   # plugins distintos, mismo formato
# Falsos positivos encontrados al correr sobre el backlog real: bugs distintos que
# sólo se parecen por la plantilla que ambos tocan.
FALSE_POS_BOILER = [132524, 132525]   # .env.example + .github/, código distinto
FALSE_POS_AUTHOR = [132351, 132603]   # mismo autor, skills distintos
# Positivo adicional del backlog real: autores DISTINTOS, mismo archivo, mismo
# problema. Es el caso que un clon real produce siempre.
REAL_CLONE = [132487, 132618]         # ambos tocan agent/context_compressor.py
R = []


def check(name, ok, detail=""):
    R.append((ok, name, detail))


def main() -> int:
    nums = POSITIVE + NEGATIVE + FALSE_POS_BOILER + FALSE_POS_AUTHOR + REAL_CLONE
    prs = {}
    for n in nums:
        p = fd.load_pr(n, fd.REPO)
        if p:
            prs[n] = p
    print(f"\n{'='*74}\nfind_dupes.py — control\n{'='*74}")
    print(f"  cargadas {len(prs)}/{len(nums)} PRs\n")

    ids_all = {n: fd.identifiers({"files": p["_files_raw"], "title": p["title"], "body": ""})
               for n, p in prs.items()}
    df = fd.document_freq(ids_all)
    print(f"  tokens de formato detectados (document freq > 60%): {len(df)}")
    print(f"    {sorted(df)[:8]}\n")

    # los tokens de formato deben ser MUCHOS menos en el corpus mixto que
    # separando positivos de negativos
    pos_ids = {n: ids_all[n] for n in POSITIVE if n in ids_all}
    neg_ids = {n: ids_all[n] for n in NEGATIVE if n in ids_all}
    check("document_freq sobre corpus mixto quita tokens de formato",
          len(df) > 0, f"{len(df)} tokens")

    groups, conflicts = fd.cluster(prs, fd.THRESHOLD)
    grouped = {frozenset(x["n"] for x in g) for g in groups}

    print("\n  grupos detectados:")
    for g in grouped:
        kind = ("POSITIVO(sistema)" if set(POSITIVE) & g else
                "FP-boilerplate" if set(FALSE_POS_BOILER) & g else
                "FP-mismo-autor" if set(FALSE_POS_AUTHOR) & g else
                "NEGATIVO(catalogo)" if set(NEGATIVE) & g else "mixto")
        print(f"    {sorted(g)}  <- {kind}")

    pos_ok = any(set(POSITIVE).issubset(g) for g in grouped)
    check("POSITIVO: las 5 PRs del mismo bug en un grupo", pos_ok,
          f"grupos={len(grouped)}")

    clone_ok = any(set(REAL_CLONE).issubset(g) for g in grouped)
    check("POSITIVO: clon real (autores distintos) detectado", clone_ok,
          f"esperado grupo con {REAL_CLONE}")

    # ningún grupo puede contener dos plugins distintos del catálogo
    bad = [sorted(set(NEGATIVE) & g) for g in grouped if len(set(NEGATIVE) & g) > 1]
    check("NEGATIVO: ningun grupo junta 2+ plugins distintos", not bad,
          f"violaciones={bad}")

    check("NEGATIVO: plugins no forman grupo propio",
          not any(g and set(g).issubset(set(NEGATIVE)) for g in grouped))

    # Los dos pares de "mismo autor, bugs distintos" SÍ se agrupan: el solape existe
    # y genera conflictos de merge. Lo que cambia es el MENSAJE — nunca dice
    # "duplicado" para ellos. Ninguna métrica los separa (#132524/#132525 tiene
    # similitud 0.634, más alta que 7 de 10 pares duplicados reales); separarlos
    # por métrica sería mentir sobre lo que el detector sabe.
    ov1 = [sorted(g & set(FALSE_POS_BOILER)) for g in grouped
           if len(set(FALSE_POS_BOILER) & g) == 2]
    check("mismo autor / bugs distintos: SI se agrupan (el solape es real)",
          len(ov1) == 1, f"agrupados={ov1}")

    ov2 = [sorted(g & set(FALSE_POS_AUTHOR)) for g in grouped
           if len(set(FALSE_POS_AUTHOR) & g) == 2]
    check("mismo autor / skills distintas: SI se agrupan", len(ov2) == 1, f"agrupados={ov2}")

    # el mensaje de cada caso dice lo correcto
    msg_dup = fd.report([prs[n] for n in POSITIVE], conflicts)
    check("mensaje del clon real dice 'Possible duplicate'",
          "Possible duplicate" in msg_dup, "")
    check("mensaje del clon NO dice 'likely distinct'",
          "likely distinct" not in msg_dup, "")

    ovgrp = next(([prs[n] for n in g] for g in grouped
                  if set(FALSE_POS_BOILER) <= g or set(FALSE_POS_AUTHOR) <= g), None)
    if ovgrp:
        msg_ov = fd.report(ovgrp, conflicts)
        check("mensaje de solape NO dice 'Possible duplicate'",
              "Possible duplicate" not in msg_ov, "")
        check("mensaje de solape dice 'likely distinct'",
              "likely distinct" in msg_ov, "")
        check("el aviso de solape pide un humano, no una acción automática",
              "a human decides" in msg_ov.lower(), "")

    # y aserciones directas sobre los helpers
    a, b = prs.get(132524), prs.get(132525)
    if a and b:
        # Estas SÍ comparten código (el autor arrastra su conjunto de trabajo).
        # Lo que NO coincide es la intención declarada — y resulta que ninguna la
        # declaró: los dos cuerpos son la plantilla del repo sin rellenar.
        check("has_code_signal True para el par del mismo autor (comparte código)",
              fd.has_code_signal(a, b) is True, "esperado True")
        check("is_template_body True en #132524 (6 placeholders sin rellenar)",
              fd.is_template_body(a) is True, "esperado True")
        check("is_template_body True en #132525", fd.is_template_body(b) is True, "esperado True")
        check("titles_agree False: cuerpos sin rellenar, no se declara intención",
              fd.titles_agree(a, b) is False, "esperado False")
    for n in (132351, 132603, 103086, 124082):
        p = prs.get(n)
        if p:
            check(f"is_template_body False en #{n} (cuerpo real)",
                  fd.is_template_body(p) is False, "esperado False")
    c, d = prs.get(103086), prs.get(108072)
    if c and d:
        check("titles_agree True: dos PRs del mismo bug de systemd",
              fd.titles_agree(c, d) is True, "esperado True")

    # los positivos deben seguir detectandose DESPUES del prune
    pos_pruned = {n: (ids_all[n] - df) for n in POSITIVE if n in ids_all}
    sizes = [len(v) for v in pos_pruned.values()]
    check("POSITIVO: sobrevive al prune (>MIN_TOKENS)", all(s >= fd.MIN_TOKENS for s in sizes),
          f"tamanos={sizes}")

    for ok, name, detail in R:
        print(f"  [{'ok  ' if ok else 'FALLA'}] {name:52} {detail}")
    passed = sum(1 for ok, *_ in R if ok)
    print(f"\n  {passed}/{len(R)} correctos")
    return 0 if passed == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())