#!/usr/bin/env python3
"""Тонкая CLI-обёртка над VetDB REST API (SPEC §4).

Базовый URL берётся из env `VETDB_API_BASE` (по умолчанию http://localhost:8011).
Секретов не содержит — API внутренний, без аутентификации. Вывод — JSON.

Примеры:
    export VETDB_API_BASE=http://localhost:8011
    python vetdb.py search "парвовироз" --scope books --limit 5
    python vetdb.py sources --q инфекц
    python vetdb.py toc 1
    python vetdb.py page 1 4
    python vetdb.py preparations --q ампициллин
    python vetdb.py diseases
"""
import argparse
import json
import os
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = os.environ.get("VETDB_API_BASE", "http://localhost:8011").rstrip("/")


def _get(path, params=None):
    url = BASE + path
    params = {k: v for k, v in (params or {}).items() if v is not None}
    if params:
        url += "?" + urlencode(params)
    with urlopen(url) as resp:  # noqa: S310 (внутренний URL)
        return json.load(resp)


def _print(data):
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def main(argv=None):
    p = argparse.ArgumentParser(prog="vetdb", description="VetDB API CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="полнотекстовый поиск")
    s.add_argument("q")
    s.add_argument("--scope", default="all", choices=["all", "books", "pharma", "diseases"])
    s.add_argument("--source-id", type=int)
    s.add_argument("--limit", type=int, default=20)

    so = sub.add_parser("sources", help="список источников")
    so.add_argument("--q")

    sd = sub.add_parser("source", help="карточка источника")
    sd.add_argument("id", type=int)

    t = sub.add_parser("toc", help="оглавление книги")
    t.add_argument("id", type=int)

    pg = sub.add_parser("page", help="текст страницы (page_index 0-based)")
    pg.add_argument("id", type=int)
    pg.add_argument("index", type=int)

    pr = sub.add_parser("preparations", help="список препаратов")
    pr.add_argument("--q")
    pr.add_argument("--drug-class")
    pr.add_argument("--animal")
    pr.add_argument("--limit", type=int, default=50)

    prd = sub.add_parser("preparation", help="карточка препарата")
    prd.add_argument("id", type=int)

    di = sub.add_parser("diseases", help="список болезней")
    di.add_argument("--species")

    did = sub.add_parser("disease", help="карточка болезни")
    did.add_argument("id", type=int)

    a = p.parse_args(argv)

    if a.cmd == "search":
        _print(_get("/api/search", {"q": a.q, "scope": a.scope,
                                    "source_id": a.source_id, "limit": a.limit}))
    elif a.cmd == "sources":
        _print(_get("/api/sources", {"q": a.q}))
    elif a.cmd == "source":
        _print(_get(f"/api/sources/{a.id}"))
    elif a.cmd == "toc":
        _print(_get(f"/api/sources/{a.id}/toc"))
    elif a.cmd == "page":
        _print(_get(f"/api/sources/{a.id}/pages/{a.index}"))
    elif a.cmd == "preparations":
        _print(_get("/api/preparations", {"q": a.q, "drug_class": a.drug_class,
                                          "animal": a.animal, "limit": a.limit}))
    elif a.cmd == "preparation":
        _print(_get(f"/api/preparations/{a.id}"))
    elif a.cmd == "diseases":
        _print(_get("/api/diseases", {"species": a.species}))
    elif a.cmd == "disease":
        _print(_get(f"/api/diseases/{a.id}"))


if __name__ == "__main__":
    main()
