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


# --- Экранированные скобки в строках текста ---------------------------------------------------------------
# Индексатор (pdf-inspector) обрывает разбор страницы на `\(` или `\)` внутри строки. Просмотрщики читают такой
# текст правильно, поэтому PyMuPDF ошибки не видит. Замена на восьмеричные коды \050 и \051 по стандарту
# равнозначна, но индексатор её читает.
import re  # noqa: E402

INLINE_IMAGE = re.compile(rb"(?<![\w/])BI(?=\s)")


def escape_parens_in_stream(s: bytes):
    """Заменяет скобки внутри строк в скобках (экранированные и вложенные) на \\050 и \\051.

    Возвращает (новый поток, число замен). Строки в `<...>` и комментарии не затрагиваются."""
    out = bytearray()
    i, n, changed = 0, len(s), 0
    while i < n:
        c = s[i:i + 1]
        if c == b"(":
            j, depth, buf = i + 1, 1, bytearray(b"(")
            while j < n and depth:
                b = s[j:j + 1]
                if b == b"\\":
                    nxt = s[j + 1:j + 2]
                    if nxt in (b"(", b")"):
                        buf += b"\\050" if nxt == b"(" else b"\\051"
                        changed += 1
                    else:
                        buf += s[j:j + 2]
                    j += 2
                    continue
                if b == b"(":
                    depth += 1
                    buf += b"\\050"
                    changed += 1
                elif b == b")":
                    depth -= 1
                    if depth:
                        buf += b"\\051"
                        changed += 1
                    else:
                        buf += b")"
                else:
                    buf += b
                j += 1
            out += buf
            i = j
            continue
        if c == b"%":  # комментарий до конца строки
            k = s.find(b"\n", i)
            k = n if k < 0 else k
            out += s[i:k]
            i = k
            continue
        out += c
        i += 1
    return bytes(out), changed


def count_escaped_paren_pages(doc, pages=None) -> int:
    """Сколько из страниц (всех или переданных номеров с 0) содержат `\\(` или `\\)` в потоке содержимого."""
    cnt = 0
    for i in (pages if pages is not None else range(len(doc))):
        try:
            for x in doc[i].get_contents():
                s = doc.xref_stream(x) or b""
                if (b"\\(" in s or b"\\)" in s) and not INLINE_IMAGE.search(s):
                    cnt += 1
                    break
        except Exception:  # noqa: BLE001
            continue
    return cnt


def escape_parens(doc):
    """Исправляет потоки содержимого всех страниц. Возвращает (число замен, пропущено потоков со встроенными картинками)."""
    total = skipped = 0
    for page in doc:
        for x in page.get_contents():
            s = doc.xref_stream(x)
            if s is None or not (b"\\(" in s or b"\\)" in s):
                continue
            if INLINE_IMAGE.search(s):  # двоичные данные картинки: безопасно разбирать нельзя
                skipped += 1
                continue
            s2, k = escape_parens_in_stream(s)
            if k:
                doc.update_stream(x, s2)
                total += k
    return total, skipped


# --- Неполные карты ToUnicode ---------------------------------------------------------------------------
# У части Type1/TrueType-шрифтов в кодировке (Differences) есть имена глифов для всех кодов, а в ToUnicode часть
# кодов пропущена (чаще всего дефис, тире, кавычки, иногда отдельные буквы). PyMuPDF тогда выдаёт управляющий
# символ, а индексатор теряет знак совсем: «Блок-схема» превращается в «Блоксхема», «6-й» в «6й». Недостающие
# коды достраиваются по имени глифа (таблица имён ниже: латиница, знаки, кириллица afii100xx, uniXXXX).
NAMES = {'space':0x20,'exclam':0x21,'quotedbl':0x22,'numbersign':0x23,'dollar':0x24,'percent':0x25,'ampersand':0x26,
 'quotesingle':0x27,'parenleft':0x28,'parenright':0x29,'asterisk':0x2A,'plus':0x2B,'comma':0x2C,'hyphen':0x2D,
 'hyphenminus':0x2D,'period':0x2E,'slash':0x2F,'colon':0x3A,'semicolon':0x3B,'less':0x3C,'equal':0x3D,'greater':0x3E,
 'question':0x3F,'at':0x40,'bracketleft':0x5B,'backslash':0x5C,'bracketright':0x5D,'underscore':0x5F,'braceleft':0x7B,
 'bar':0x7C,'braceright':0x7D,'asciitilde':0x7E,'endash':0x2013,'emdash':0x2014,'quoteleft':0x2018,'quoteright':0x2019,
 'quotedblleft':0x201C,'quotedblright':0x201D,'quotedblbase':0x201E,'quotesinglbase':0x201A,'bullet':0x2022,
 'ellipsis':0x2026,'guillemotleft':0xAB,'guillemotright':0xBB,'section':0xA7,'degree':0xB0,'plusminus':0xB1,
 'multiply':0xD7,'nbspace':0xA0,'sfthyphen':0xAD,'copyright':0xA9,'registered':0xAE,'trademark':0x2122,'minus':0x2212,
 'fi':0xFB01,'fl':0xFB02,'dagger':0x2020,'paragraph':0xB6,'afii61352':0x2116,'numero':0x2116,'percent':0x25,
 'zero':0x30,'one':0x31,'two':0x32,'three':0x33,'four':0x34,'five':0x35,'six':0x36,'seven':0x37,'eight':0x38,'nine':0x39,
 'afii10023':0x401,'afii10071':0x451}
def glyph_to_unicode(n):
    if n in NAMES: return NAMES[n]
    if len(n)==1 and n.isalpha(): return ord(n)
    m=re.fullmatch(r'afii(\d+)',n)
    if m:
        v=int(m.group(1))
        if 10017<=v<=10022: return 0x410+v-10017
        if 10024<=v<=10049: return 0x416+v-10024
        if 10065<=v<=10070: return 0x430+v-10065
        if 10072<=v<=10097: return 0x436+v-10072
    m=re.fullmatch(r'uni([0-9A-Fa-f]{4})',n)
    if m: return int(m.group(1),16)
    return None
def parse_differences(s):
    out={}; code=None
    for tok in re.findall(r'/[^\s/\[\]]+|\d+',s):
        if tok[0]=='/':
            if code is not None: out[code]=tok[1:]; code+=1
        else: code=int(tok)
    return out
def covered(cmap):
    cov=set()
    # Нужны только однобайтовые коды (шрифты с Differences простые). Диапазоны вида <00000000> <FFFFFFFF> режем до 0-255:
    # иначе перебор миллиардов кодов съедает всю память
    for a,b,_ in re.findall(r'<([0-9a-fA-F]+)>\s*<([0-9a-fA-F]+)>\s*(<[0-9a-fA-F]+>|\[[^\]]*\])',cmap):
        lo,hi=int(a,16),min(int(b,16),255)
        if lo<=hi:
            cov.update(range(lo,hi+1))
    for m in re.finditer(r'beginbfchar(.*?)endbfchar',cmap,re.S):
        for a in re.findall(r'<([0-9a-fA-F]+)>\s*<[0-9a-fA-F]+>',m.group(1)):
            if int(a,16)<=255: cov.add(int(a,16))
    return cov
def complete_tounicode(doc, dry_run=False):
    fixed=added=0
    for xr in range(1,doc.xref_length()):
        try:
            if doc.xref_get_key(xr,'Type')[1]!='/Font' or doc.xref_get_key(xr,'Subtype')[1] not in ('/Type1','/TrueType'): continue
            tu=doc.xref_get_key(xr,'ToUnicode')
            if tu[0]!='xref': continue
            enc=doc.xref_get_key(xr,'Encoding')
            if enc[0]=='xref': eo=doc.xref_object(int(enc[1].split()[0]))
            elif enc[0]=='dict': eo=enc[1]
            else: continue
            if 'Differences' not in eo: continue
            diff=parse_differences(eo[eo.index('Differences'):])
            n=int(tu[1].split()[0]); cmap=doc.xref_stream(n).decode('latin1')
            cov=covered(cmap)
            new=[(c,glyph_to_unicode(g)) for c,g in diff.items() if c not in cov and glyph_to_unicode(g)]
            if not new: continue
            if dry_run:
                fixed+=1; added+=len(new); continue
            block=f'{len(new)} beginbfchar\n'+''.join(f'<{c:02x}> <{u:04X}>\n' for c,u in new)+'endbfchar\n'
            i=cmap.rfind('endcmap')
            if i<0: continue
            doc.update_stream(n,(cmap[:i]+block+cmap[i:]).encode('latin1')); fixed+=1; added+=len(new)
        except Exception: continue
    return fixed,added


# --- Кириллица в шрифте с кодировкой WinAnsi ----------------------------------------------------------------
# Некоторые издатели объявляют шрифт заголовков или колонтитулов как WinAnsiEncoding без карты ToUnicode, хотя
# в строках лежат коды кодовой страницы 1251. Просмотрщик рисует нужные глифы, а текст читается как «Îãëàâëåíèå».
# Индексатор помечает такие страницы как испорченные, и страница теряется целиком. Карта ToUnicode на основе
# cp1251 это исправляет (вид страницы не меняется).
def _cp1251_cmap() -> bytes:
    rows = []
    for c in range(0x20, 0x100):
        u = bytes([c]).decode("cp1251", errors="ignore")
        if u:
            rows.append(f"<{c:02x}> <{ord(u):04x}>")
    return ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Adobe-Identity-UCS def "
            "/CMapType 2 def\n1 begincodespacerange <00> <ff> endcodespacerange\n%d beginbfchar\n" % len(rows)
            + "\n".join(rows) + "\nendbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end").encode()


def _latin1_identity_codes(cmap: str) -> int:
    """Сколько кодов 0xC0-0xFF карта ToUnicode отображает в тот же код Latin-1 (U+00C0-U+00FF): признак того, что
    кириллица в cp1251 записана в карте как «Ã», «Ð» и т. п."""
    n = 0
    for a, b, u in re.findall(r"<([0-9a-fA-F]{2})>\s*<([0-9a-fA-F]{2})>\s*<([0-9a-fA-F]{4})>", cmap):
        a, b, u = int(a, 16), int(b, 16), int(u, 16)
        if a >= 0xC0 and u == a:
            n += b - a + 1
    for a, u in re.findall(r"^<([0-9a-fA-F]{2})>\s*<([0-9a-fA-F]{4})>", cmap, re.M):
        a, u = int(a, 16), int(u, 16)
        if a >= 0xC0 and u == a:
            n += 1
    return n


def winansi_cyrillic_fonts(doc, sample_pages=40):
    """Шрифты, чей текст на деле cp1251, а не Latin-1: WinAnsi без ToUnicode, либо с картой ToUnicode, в которой коды
    0xC0-0xFF отображены в Latin-1. Возвращает {xref: BaseFont}."""
    cand = {}
    for xr in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xr, "Type")[1] != "/Font" or doc.xref_get_key(xr, "Subtype")[1] not in ("/Type1", "/TrueType"):
                continue
            tu = doc.xref_get_key(xr, "ToUnicode")
            if tu[0] == "null":
                if doc.xref_get_key(xr, "Encoding")[1] != "/WinAnsiEncoding":
                    continue
            elif tu[0] != "xref" or _latin1_identity_codes(doc.xref_stream(int(tu[1].split()[0])).decode("latin1")) < 10:
                continue
            base = doc.xref_get_key(xr, "BaseFont")[1].lstrip("/")
            cand[xr] = base.split("+")[-1]
        except Exception:  # noqa: BLE001
            continue
    if not cand:
        return {}
    text = {}
    step = max(1, len(doc) // sample_pages)
    for i in range(0, len(doc), step):
        try:
            for b in doc[i].get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    for sp in ln["spans"]:
                        text[sp["font"]] = text.get(sp["font"], "") + sp["text"]
        except Exception:  # noqa: BLE001
            continue
    found = {}
    for xr, name in cand.items():
        letters = [c for c in text.get(name, "") if c.isalpha()]
        if len(letters) < 5:
            continue
        high = [c for c in letters if "\u00c0" <= c <= "\u00ff"]
        if len(high) / len(letters) < 0.6:
            continue
        dec = "".join(high).encode("cp1252", errors="ignore").decode("cp1251", errors="ignore")
        if dec and sum("\u0400" <= c <= "\u04ff" for c in dec) / len(dec) > 0.9:
            found[xr] = name
    return found


def fix_winansi_cyrillic(doc, dry_run=False):
    fonts = winansi_cyrillic_fonts(doc)
    if dry_run or not fonts:
        return len(fonts)
    for xr in fonts:
        tu = doc.xref_get_key(xr, "ToUnicode")
        if tu[0] == "xref":                                   # карта есть, но неверная: заменяем её содержимое
            doc.update_stream(int(tu[1].split()[0]), _cp1251_cmap())
            continue
        s = doc.get_new_xref()
        doc.update_object(s, "<<>>")
        doc.update_stream(s, _cp1251_cmap())
        doc.xref_set_key(xr, "ToUnicode", f"{s} 0 R")
    return len(fonts)


# --- Невидимый текст поверх скана -----------------------------------------------------------------------------
# Старый OCR (ABBYY FineReader, iText) кладёт после картинки страницы текст с режимом `3 Tr` (невидимый).
# Индексатор такой текст не читает вообще. Правка: режим `0 Tr` и перенос картинки в конец потока: текст
# оказывается под непрозрачной картинкой, глазом страница не меняется, а индексатор видит обычный текст.
SCAN_THEN_TEXT = re.compile(rb"(q [^\n]*? Do Q)\s*(BT .*)$", re.S)


def _hidden_text_pages(doc, pages):
    out = []
    for i in pages:
        try:
            cs = doc[i].get_contents()
            if len(cs) != 1:
                continue
            m = SCAN_THEN_TEXT.match(doc.xref_stream(cs[0]) or b"")
            if m and b" 3 Tr" in m.group(2):
                out.append(i)
        except Exception:  # noqa: BLE001
            continue
    return out


def count_hidden_text_pages(doc, sample_pages=40):
    """Доля (0..1) проверенных страниц со скан-картинкой и невидимым текстом после неё."""
    idx = list(range(0, len(doc), max(1, len(doc) // sample_pages)))
    return len(_hidden_text_pages(doc, idx)) / max(1, len(idx))


def reveal_hidden_text(doc):
    n = 0
    for i in _hidden_text_pages(doc, range(len(doc))):
        x = doc[i].get_contents()[0]
        m = SCAN_THEN_TEXT.match(doc.xref_stream(x))
        doc.update_stream(x, m.group(2).replace(b" 3 Tr", b" 0 Tr") + b"\n" + m.group(1) + b"\n")
        n += 1
    return n


# --- Белые подложки под строками и повторяющиеся колонтитулы ----------------------------------------------
# Некоторые PDF (например, из электронных библиотек) хранят под каждой строкой невидимую белую закрашенную
# полосу `1 g x y w h re f`. Для глаза она незаметна, а индексатор принимает стопку одинаковых полос за линейки
# таблицы, и текст страницы перемешивается. Полосы удаляются из потока содержимого (вид страницы не меняется).
WHITE_BOX = re.compile(rb"(?:(?<=\n)|^)(?:1 g|1 1 1 rg)\s+-?[\d.]+ -?[\d.]+ -?[\d.]+ -?[\d.]+ re\s+f\s*\n?")


def count_white_box_pages(doc, pages=None) -> int:
    """Сколько из страниц (всех или переданных номеров с 0) содержат не меньше 5 белых закрашенных полос."""
    cnt = 0
    for i in (pages if pages is not None else range(len(doc))):
        try:
            found = sum(len(WHITE_BOX.findall(doc.xref_stream(x) or b"")) for x in doc[i].get_contents())
        except Exception:  # noqa: BLE001
            continue
        cnt += found >= 5
    return cnt


def strip_white_boxes(doc) -> int:
    """Удаляет белые закрашенные полосы из потоков содержимого страниц. Возвращает число удалённых."""
    removed = 0
    for page in doc:
        for x in page.get_contents():
            s = doc.xref_stream(x)
            if s is None:
                continue
            s2, k = WHITE_BOX.subn(b"", s)
            if k:
                doc.update_stream(x, s2)
                removed += k
    return removed


def _normalize_running(text: str) -> str:
    return re.sub(r"\d+", "#", re.sub(r"\s+", " ", text)).strip()


def _running_candidate(raw: str) -> bool:
    """Годится для удаления как колонтитул: не меньше 3 букв либо только знаки препинания (разделитель «||»).
    Номера страниц (цифры) не годятся."""
    raw = raw.strip()
    return sum(c.isalpha() for c in raw) >= 3 or (len(raw) >= 2 and not any(c.isalnum() for c in raw))


def find_running_text(doc, band=0.08, min_share=0.5, pages=None):
    """Тексты у верхнего и нижнего краёв, которые повторяются на большинстве страниц (колонтитулы, штампы скана).

    Номера страниц (одни цифры) не считаются. Возвращает {нормализованный текст: число страниц}. pages это
    номера страниц с 0 для выборочной проверки (по умолчанию все)."""
    counts = {}
    idx = list(pages) if pages is not None else list(range(len(doc)))
    n = len(idx)
    for i in idx:
        page = doc[i]
        h = page.rect.height
        seen = set()
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    y0, y1 = s["bbox"][1], s["bbox"][3]
                    norm = _normalize_running(s["text"])
                    if (y1 < band * h or y0 > (1 - band) * h) and _running_candidate(s["text"]):
                        seen.add(norm)
        for norm in seen:
            counts[norm] = counts.get(norm, 0) + 1
    return {t: c for t, c in counts.items() if c >= min_share * n and c >= 3}


def strip_running_text(doc, texts, band=0.08) -> int:
    """Удаляет у краёв страниц спаны с заданным (нормализованным) текстом. Возвращает число удалённых спанов."""
    removed = 0
    for page in doc:
        h = page.rect.height
        rects = []
        for b in page.get_text("dict")["blocks"]:
            for ln in b.get("lines", []):
                for s in ln["spans"]:
                    y0, y1 = s["bbox"][1], s["bbox"][3]
                    if ((y1 < band * h or y0 > (1 - band) * h) and _running_candidate(s["text"])
                            and _normalize_running(s["text"]) in texts):
                        rects.append(fitz.Rect(s["bbox"]))
        for r in rects:
            page.add_redact_annot(r + (-1, -1, 1, 3))     # запас снизу: подчёркивание ссылки лежит под текстом
        if rects:
            # ссылки, висящие на удаляемом тексте (адрес сайта из штампа), остались бы пустыми рамками
            for link in page.get_links():
                if any(fitz.Rect(link["from"]).intersects(r) for r in rects):
                    page.delete_link(link)
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                                  text=fitz.PDF_REDACT_TEXT_REMOVE)
            removed += len(rects)
    return removed


# --- Знак минус U+2212 в картах ToUnicode ------------------------------------------------------------------
# Если карты ToUnicode шрифтов отдают знак минус (U+2212) вместо дефиса, индексатор на страницах с большим числом
# таких знаков (листинги кода, строки «−−−−» в выводе консоли) считает, что текста нет («нужен OCR»), и выдаёт
# пустую страницу. Например, книга в 683 страницы (LuaTeX): 187 страниц без единого символа в индексе. Замена
# целевого символа на обычный дефис-минус возвращает страницы целиком.
def remap_minus(doc, dry_run=False):
    """Заменяет U+2212 на U+002D в картах ToUnicode. Возвращает число изменённых карт."""
    changed = 0
    for xr in range(1, doc.xref_length()):
        try:
            if doc.xref_get_key(xr, "Type")[1] != "/Font":
                continue
            tu = doc.xref_get_key(xr, "ToUnicode")
            if tu[0] != "xref":
                continue
            n = int(tu[1].split()[0])
            stream = doc.xref_stream(n)
            if stream is None or len(stream) > 2_000_000 or b"2212" not in stream.upper():
                continue
            new = re.sub(rb"(<[0-9A-Fa-f]+>\s*)<2212>", rb"\1<002D>", stream)
            if new == stream:
                continue
            changed += 1
            if not dry_run:
                doc.update_stream(n, new)
        except Exception:  # noqa: BLE001
            continue
    return changed


# --- Один огромный текстовый блок на странице -------------------------------------------------------------------
# У части PDF (старые сканы со скрытым слоем OCR от iText и подобных) весь текст страницы лежит в одном объекте
# BT ... ET с десятками перемещений Tm. Индексатор такой блок не читает: из 3000 символов страницы остаётся
# колонтитул из 70. Блок разбивается на отдельные BT/ET перед каждым Tm (параметры шрифта, Tc, Tw, Tz, Tr
# сохраняются между текстовыми объектами, положение задаёт сам Tm), результат для глаза не меняется.
TM_OP = re.compile(rb"((?:-?[\d.]+\s+){6})Tm")


def long_text_object_pages(doc, min_tm=20):
    """Страницы с одним-двумя текстовыми блоками и множеством перемещений Tm (номера с 0)."""
    found = []
    for i, page in enumerate(doc):
        try:
            stream = b"".join(doc.xref_stream(x) or b"" for x in page.get_contents())
        except Exception:  # noqa: BLE001
            continue
        bt = len(re.findall(rb"\bBT\b", stream))
        tm = len(TM_OP.findall(stream))
        if bt and tm >= min_tm and tm >= 10 * bt:
            found.append(i)
    return found


def split_text_objects(doc, pages):
    """Разбивает текстовые блоки на указанных страницах. Возвращает число вставленных разрывов."""
    inserted = 0
    for i in pages:
        first = True
        for x in doc[i].get_contents():
            stream = doc.xref_stream(x)
            if stream is None:
                continue

            def sub(m):
                nonlocal first, inserted
                if first:
                    first = False
                    return m.group(0)
                inserted += 1
                return b"ET\nBT\n" + m.group(0)

            new = TM_OP.sub(sub, stream)
            if new != stream:
                doc.update_stream(x, new)
    return inserted
