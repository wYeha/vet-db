"""Мягкий видовой буст онтологии-чата (без LLM, без записи в БД).

Вид животного из вопроса пользователя (`detect_query_species`) сравнивается с видом
источника (`source_species`), вычисленным на лету из `sources.title/description`.
`rerank_by_species` — СТАБИЛЬНАЯ пересортировка по трём ярусам, ничего не отбрасывает:
  0 — вид источника ∈ видам запроса;
  1 — вид источника неизвестен/нейтральный (справочники, ПЦР и т.п.) или нет source_id;
  2 — вид источника иной.
Словарь только по видам, реально присутствующим в данных (swine, avian). КРС в корпусе
нет — `cattle` добавить в SPECIES при появлении книг. Классификация — уровень книги.
"""
import logging
import re
import sqlite3
from typing import Callable, Dict, FrozenSet, List, Optional

from .schemas import SearchHit

log = logging.getLogger("vetai.species")

# Регэкспы по lower-тексту с ё→е; \b в начале слова и явные формы, чтобы не ловить
# «свинец», «курс», «курение», «густой», «утро».
SPECIES: Dict[str, "re.Pattern[str]"] = {
    "swine": re.compile(
        r"\b(?:свин(?:ой|ая|ое|ые|ую|ого|ому|ым|ыми|ых|ь\w*|ей|ы|ам\w*|ов\w*|ин\w*|к[аиуе]\w*|ск\w*|ом|ах)"
        r"|порос\w*|хряк\w*|свиномат\w*|свиновод\w*|свиноферм\w*"
        r"|pigs?|swine|sows?|piglets?|boars?|porcine|hogs?)\b"
    ),
    "avian": re.compile(
        r"\b(?:птиц\w*|птичь\w*|птенц\w*|бройлер\w*|цыпл\w*|несушк\w*|индейк\w*"
        r"|индюш\w*|индюк\w*|утк(?:а|и|е|у|ой|ам\w*|ами|ах)|уток|утят\w*|утин\w*|птичк\w*|петух\w*|курятник\w*"
        r"|кур(?:а|ы|е|у|ой|ам|ами|ах|иц\w*|ин\w*|ят\w*)?"
        r"|гус(?:ь|я|ей|и|ям\w*|ят\w*)"
        r"|poultry|chickens?|broilers?|hens?|turkeys?|ducks?|avian|birds?)\b"
    ),
}


_GUINEA_PIG = re.compile(r"\bморск\w*\s+свинк\w*|\bguinea[\s-]+pigs?\b")


def _norm(text: Optional[str]) -> str:
    # «морская свинка» (грызун) — не swine: вырезаем до поиска
    return _GUINEA_PIG.sub(" ", (text or "").lower().replace("ё", "е"))


def detect_query_species(text: Optional[str]) -> FrozenSet[str]:
    """Виды животных, упомянутые в исходном сообщении пользователя."""
    t = _norm(text)
    return frozenset(name for name, rx in SPECIES.items() if rx.search(t))


def source_species(db) -> Dict[int, Optional[str]]:
    """{source_id: вид | None} по `sources.title + description` (один запрос).

    Вид присваивается, если top >= 2 вхождений И top >= 2 × runner-up; иначе None
    (нейтральный/смешанный). Нет `description` → только заголовок; ошибка схемы → {}.
    """
    try:
        try:
            rows = db.execute("SELECT id, title, description FROM sources").fetchall()
        except sqlite3.OperationalError:
            rows = db.execute("SELECT id, title, NULL AS description FROM sources").fetchall()
    except sqlite3.OperationalError as exc:
        log.warning("species: sources unavailable, boost disabled: %s", exc)
        return {}
    out: Dict[int, Optional[str]] = {}
    for r in rows:
        t = _norm(f"{r['title'] or ''} {r['description'] or ''}")
        counts = sorted(
            ((len(rx.findall(t)), name) for name, rx in SPECIES.items()), reverse=True
        )
        top, runner = counts[0][0], (counts[1][0] if len(counts) > 1 else 0)
        out[int(r["id"])] = counts[0][1] if top >= 2 and top >= 2 * runner else None
    return out


def tier_of(hit: SearchHit, query_species: FrozenSet[str],
            src_species: Dict[int, Optional[str]]) -> int:
    sp = src_species.get(hit.source_id) if hit.source_id is not None else None
    if sp is None:
        return 1
    return 0 if sp in query_species else 2


def species_key(query_species: FrozenSet[str],
                src_species: Dict[int, Optional[str]]) -> Optional[Callable[[SearchHit], int]]:
    """Ключ яруса для `sorted`; None, если буст не нужен (нет вида / нет данных)."""
    if not query_species or not src_species:
        return None
    return lambda h: tier_of(h, query_species, src_species) if h.type == "page" else 1


def rerank_by_species(hits: List[SearchHit], query_species: FrozenSet[str],
                      src_species: Dict[int, Optional[str]]) -> List[SearchHit]:
    """Стабильная пересортировка по ярусам (см. докстринг модуля). Пустой вид или
    пустой `src_species` → список без изменений. Не-page хиты — ярус 1."""
    key = species_key(query_species, src_species)
    if key is None:
        return list(hits)
    result = sorted(hits, key=key)
    log.info(
        "species: query=%s tiers=%d/%d/%d", sorted(query_species),
        *[sum(1 for h in hits if key(h) == t) for t in (0, 1, 2)],
    )
    return result
