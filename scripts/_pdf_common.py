"""Общие функции для проверки и ремонта PDF (check_pdf.py, repair_pdf.py)."""
import pymupdf as fitz

fitz.TOOLS.mupdf_display_errors(False)   # сообщения MuPDF собираем сами, а не печатаем потоком

# Сообщения MuPDF, после которых текст страницы вероятно потерян или обрезан. Остальное (нет cmap у шрифта,
# нестандартные операторы) страницу не портит.
DAMAGE_MARKERS = ("zlib error", "read error", "page may not", "too many syntax errors", "stack overflow",
                  "cannot find page", "cycle in page tree")


def is_missing_page(doc, page) -> bool:
    """В файле нет словаря этой страницы (объект потерян, дерево страниц ссылается на пустоту)."""
    return doc.xref_get_key(page.xref, "Type")[0] == "null"


def is_damaged(warnings: str) -> bool:
    """Предупреждения MuPDF при чтении страницы говорят о потере содержимого."""
    return any(m in warnings for m in DAMAGE_MARKERS)


def scan_pages(doc):
    """Читает все страницы и возвращает (потерянные страницы, страницы с повреждённым потоком), номера с 1."""
    missing, damaged = [], []
    fitz.TOOLS.mupdf_warnings()
    for i, page in enumerate(doc):
        miss = is_missing_page(doc, page)
        try:
            page.get_text()
        except Exception:  # noqa: BLE001
            damaged.append(i + 1)
            fitz.TOOLS.mupdf_warnings()
            continue
        w = fitz.TOOLS.mupdf_warnings()
        if miss:
            missing.append(i + 1)
        elif is_damaged(w):
            damaged.append(i + 1)
    return missing, damaged


def inspector_pages(path) -> int:
    """Сколько страниц видит индексатор (pdf-inspector)."""
    import pdf_inspector
    return pdf_inspector.classify_pdf(str(path)).page_count
