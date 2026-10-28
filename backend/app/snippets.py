"""Безопасные FTS-сниппеты (общие для роутера поиска и ядра онтологии).

Вынесено из routers/search.py, чтобы ядро (ontology.py) не импортировало роутер.
"""
import html
from typing import Optional

# Сниппет строится из СЫРОГО OCR-контента, где встречаются настоящие HTML-теги
# (<br>, <img> и т.п.). Чтобы v-html на клиенте их не выполнил, оборачиваем
# совпадения не в готовые <mark>, а в plaintext-плейсхолдеры из private-use зоны
# (в тексте не встречаются), затем HTML-экранируем весь фрагмент и только потом
# заменяем плейсхолдеры на настоящие <mark>/</mark>. Итог: единственный HTML в
# сниппете — доверенные <mark>, всё остальное экранировано.
_MARK_OPEN = ""
_MARK_CLOSE = ""
# параметры snippet() после номера столбца: старт-тег, конец-тег, многоточие, макс. токенов
SNIPPET_ARGS = f"'{_MARK_OPEN}', '{_MARK_CLOSE}', '…', 20"


def safe_snippet(snip: Optional[str]) -> Optional[str]:
    """Сырой FTS-сниппет с плейсхолдерами → HTML-экранированный, только доверенный <mark>."""
    if snip is None:
        return None
    return (
        html.escape(snip)
        .replace(_MARK_OPEN, "<mark>")
        .replace(_MARK_CLOSE, "</mark>")
    )


# алиасы совместимости (старые имена из routers/search.py)
_SNIP = SNIPPET_ARGS
_safe_snippet = safe_snippet
