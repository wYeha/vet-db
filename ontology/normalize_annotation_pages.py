"""Одноразовая миграция: приводит `### … (стр A–B)` в ontology/annotations/*.md к единой
конвенции «A = первая страница главы, B = последняя страница включительно, обе 1-based,
как в читалке (= page_index + 1)» и ставит маркер первой строкой файла.

Таблица книг захардкожена по сверке с текстом страниц в data/index.db
(ontology/check_annotation_pages.py), без автодетекта:
  * ("0", style) — файл в 0-based: A' = A + 1; B — по стилю книги:
      incl → B' = B + 1;
      excl → B' = B + 1, если B == next_A - 1 (глава записана inclusive), иначе B' = B
              (exclusive 0-based конец численно равен inclusive 1-based последней странице);
  * ("1", None) — файл уже 1-based: числа не трогаем, только маркер.
Файлы с маркером пропускаются (идемпотентность).

Запуск:
  python ontology/normalize_annotation_pages.py --dry-run   # печать diff заголовков
  python ontology/normalize_annotation_pages.py --apply
"""
import argparse
import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ANN = os.path.join(HERE, "annotations")

MARKER = "<!-- pages: 1-based; стр A–B = page_index+1, B включительно -->"
MARKER_RE = re.compile(r"<!--\s*pages:\s*1-based")
PAGE_RE = re.compile(r'стр\.?\s*(\d+)(?:\s*[–—-]\s*(\d+))?')   # тот же, что в load_annotations.parse
HEADER_RE = re.compile(r'^###\s+')

BOOKS = {
    "anemia_and_drugs_used_in_treatment": ("0", "excl"),
    "antimicrobial_prescribing_guidelines_for_pigs": ("0", "incl"),
    "antimicrobial_therapy_handbook": ("0", "excl"),
    "antimicrobial_usage_in_pig_production": ("0", "excl"),   # смешанный: inclusive-главы ловит правило B == next_A - 1
    "atrophic_rhinitis_of_pigs": ("1", None),
    "avian_biocheck": ("1", None),
    "avian_pathology": ("0", "incl"),
    "birds_biology_and_pathology": ("0", "incl"),
    "diseases_of_poultry": ("0", "excl"),
    "fattening_pigs_practical_guide": ("0", "incl"),
    "guide_to_working_on_growing_and_fattening": ("0", "incl"),
    "modern_pig_farming": ("0", "incl"),
    "pathological_diagnostics_diseases_pigs": ("0", "incl"),
    "pcr_test": ("1", None),
    "peisak_disease_of_pigs": ("0", "excl"),
    "pig_breeders_workshop": ("0", "incl"),
    "pigs_pharmacokinetics_dynamics_antibacterial_drugs": ("0", "excl"),
    "poultry_health_guide": ("0", "excl"),
    "practical_guide_to_broiler_health_management": ("0", "incl"),
    "sows_practical_guide": ("0", "incl"),
    "streptococcosis_dissertation": ("0", "excl"),
    "vic_articles_avian": ("1", None),
    "vic_articles_swine": ("1", None),
}


def has_marker(text):
    return bool(MARKER_RE.search(text.split("\n", 1)[0]))


def header_page(line):
    """A из строки `### …` или None."""
    if not HEADER_RE.match(line):
        return None
    m = PAGE_RE.search(line)
    return int(m.group(1)) if m else None


def convert_header(line, style, next_a):
    """Пересчитать числа в первом `стр A[–B]` строки `### …` 0-based-книги.
    Остальной текст строки сохраняется побайтно. Строки без `стр` — без изменений.

    Известное ограничение: последняя глава excl-книги (next_a is None), записанная
    inclusive, теряет 1 страницу — без следующей главы стиль не различить, B остаётся
    как есть (в текущих данных не встречается)."""
    if not HEADER_RE.match(line):
        return line
    m = PAGE_RE.search(line)
    if not m:
        return line
    a = int(m.group(1))
    out = line[:m.start(1)] + str(a + 1)
    if m.group(2) is not None:
        b = int(m.group(2))
        if style == "incl":
            b2 = b + 1
        elif style == "excl":
            b2 = b + 1 if (next_a is not None and b == next_a - 1) else b
        else:
            raise ValueError(f"неизвестный стиль B: {style!r}")
        out += line[m.end(1):m.start(2)] + str(b2) + line[m.end(2):]
    else:
        out += line[m.end(1):]
    return out


def migrate_text(text, conv, style):
    """→ (новый текст, [(старая строка, новая строка), …]). Файл с маркером не меняется."""
    if has_marker(text):
        return text, []
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(nl)
    changes = []
    if conv == "0":
        idx = [(i, header_page(l)) for i, l in enumerate(lines)]
        idx = [(i, a) for i, a in idx if a is not None]
        for k, (i, a) in enumerate(idx):
            next_a = next((a2 for _, a2 in idx[k + 1:] if a2 > a), None)
            new = convert_header(lines[i], style, next_a)
            if new != lines[i]:
                changes.append((lines[i], new))
                lines[i] = new
    elif conv != "1":
        raise ValueError(f"неизвестная конвенция: {conv!r}")
    return MARKER + nl + nl.join(lines), changes


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    for path in sorted(glob.glob(os.path.join(ANN, "*.md"))):
        slug = os.path.splitext(os.path.basename(path))[0]
        if slug not in BOOKS:
            print(f"! {slug}: нет в таблице BOOKS — пропуск")
            continue
        conv, style = BOOKS[slug]
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        if has_marker(text):
            print(f"= {slug}: маркер уже есть — пропуск")
            continue
        new, changes = migrate_text(text, conv, style)
        print(f"* {slug}: конвенция {conv}-based, стиль {style or '-'}, изменено заголовков {len(changes)}")
        if args.dry_run:
            for old, nw in changes:
                print(f"    - {old}\n    + {nw}")
        if args.apply:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(new)


if __name__ == "__main__":
    main()
