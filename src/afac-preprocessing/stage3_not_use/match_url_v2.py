from pathlib import Path
import re
import json
import jsonlines
import fitz  # PyMuPDF
from dotenv import load_dotenv
import os
from collections import defaultdict
from typing import Tuple

# Ce script a besoin de python 3.10+ pour les types dict | None, sinon remplacer par Optional[dict] et ajouter "from typing import Optional"

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # Je suis sur l'.env.test qui est le même que le .env
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

# Root
DOC_NAME = os.environ.get("DOC_NAME", "") # CHANGER SELON LES TESTS
project_root = Path(__file__).resolve().parent.parent
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
doctags_path = project_root / "data" / "output_files" / "stage2_test" / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures.doctags"
hyperlinks_path = project_root / "data" / "output_files" / "stage3_test" / DOC_NAME /f"hyperlinks_data_{DOC_NAME}.jsonl"
output_path = project_root / "data" / "output_files" / "stage3_test" / DOC_NAME /f"{DOC_NAME}_reordered_with_tables_pictures_url.doctags"

def normalize(text: str) -> str:
    """Normalise pour matching : unify dashes, remove NBSP, remove spaces around dashes, collapse spaces."""
    if not text:
        return ""
    text = str(text)
    text = text.replace("\u00A0", " ")
    text = re.sub(r"['’`´ʼ]", "'", text)
    text = re.sub(r"[\-–—]", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _build_pattern(anchor: str) -> re.Pattern:
    """Construit un pattern regex flexible pour matcher une ancre dans un texte.
    Gère les variantes de tirets ET d'apostrophes."""
    safe = re.escape(anchor)
    # Tirets flexibles
    safe = safe.replace(re.escape("-"), r"\s*[-–—]\s*")
    # Apostrophes flexibles
    safe = re.sub(r"\\['’`´ʼ]", r"['’`´ʼ]", safe)
    # Virgules et points flexibles
    safe = safe.replace(r"\,", r"\s*,\s*")
    safe = safe.replace(r"\.", r"\s*\.\s*")
    # Espaces flexibles PARTOUT
    safe = safe.replace(r"\ ", r"\s*")  # ← str.replace() instead of re.sub()
    return re.compile(safe, flags=re.IGNORECASE)

# Charge les liens extraits du PDF (get_url.py)
def load_hyperlinks(jsonl_path: Path) -> list:
    links = []
    with jsonlines.open(jsonl_path) as reader:
        for item in reader:
            if item.get("type") == "URI" and item.get("hyperlink"):
                # keep original extracted text for display, and a normalized version for matching
                raw_text = item.get("text", "") or ""
                item["text_display"] = raw_text
                item["text"] = normalize(raw_text)  # normalized text used everywhere else
                links.append(item)
    print(f"→ {len(links)} lien(s) chargé(s) depuis le JSONL")
    return links

def normalize_rect(rect: list, page_width: float, page_height: float, norm=500) -> tuple: 
    x0, y0, x1, y1 = rect
    return (
        round(x0 / page_width  * norm),
        round(y0 / page_height * norm),
        round(x1 / page_width  * norm),
        round(y1 / page_height * norm),
    )

def get_page_sizes(pdf_path: Path) -> dict:
    # Retourne {page_num (0-based): (width, height)}
    doc = fitz.open(str(pdf_path))
    sizes = {}
    for i, page in enumerate(doc):
        sizes[i] = (page.rect.width, page.rect.height)
    doc.close()
    return sizes

def parse_doctags(doctags_path: Path) -> Tuple[list, str]:
    # Parse toutes les balises avec coordonnées.
    # Retourne une liste d'éléments :
    # {page, tag, x0, y0, x1, y1, text, raw_tag, raw_start_pos}
    content = Path(doctags_path).read_text(encoding="utf-8")
    elements = []
    current_page = 0

    # Parse elements with coordinates
    for match in re.finditer(
        r'<(?!/)(?!doctag)(\w+)>' # tag ouvrant (non suivi de / ou doctag)
        r'<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>' # coordonnées
        r'(.*?)' # contenu textuel 
        r'(?=<(?!loc_)\w|$)', # lookahead pour s'arrêter avant la prochaine balise (non loc_) ou la fin du texte
        content,
        re.DOTALL
    ):
        tag = match.group(1)
        x0, y0, x1, y1 = int(match.group(2)), int(match.group(3)), int(match.group(4)), int(match.group(5)) # Convertit les coordonnées en int
        text = re.sub(r'<[^>]+>', '', match.group(6)).strip() # Nettoie les balises internes du texte

        elements.append({
            "page": current_page,
            "tag": tag,
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "text": text,
            "raw": match.group(0),
            "start_pos": match.start(),
            "is_json": False,
        })
        if tag == "page_footer":
            current_page += 1

    # Parse <text>...</text> blocks without <loc_...>
    for match in re.finditer(r'<text>\s*\n?(.*?)\n?\s*</text>', content, re.DOTALL):
        inner = match.group(1).strip()
        if "<loc_" in inner:
            continue  # skip if already parsed above
        if inner.startswith("{") and inner.endswith("}"):
            # Count opening <page_footer> tags (not closing) before this block
            page_num = len(re.findall(r'<page_footer>', content[:match.start()]))
            elements.append({
                "page": page_num,
                "tag": "text",
                "x0": 0, "y0": 0, "x1": 500, "y1": 500,
                "text": inner,
                "raw": match.group(0),
                "start_pos": match.start(),
                "is_json": True,
            })
    print(f"→ {len(elements)} élément(s) parsé(s) depuis le doctags")
    return elements, content

def overlap_ratio(r1: tuple, r2: tuple) -> float:
    # Calcule le ratio d'intersection entre deux rectangles r1 et r2.
    # r1 et r2 sont des tuples (x0, y0, x1, y1) avec des coordonnées normalisées.
    ix0 = max(r1[0], r2[0])
    iy0 = max(r1[1], r2[1])
    ix1 = min(r1[2], r2[2])
    iy1 = min(r1[3], r2[3])

    if ix1 <= ix0 or iy1 <= iy0: 
        return 0.0

    inter = (ix1 - ix0) * (iy1 - iy0)
    area1 = max(1, (r1[2]-r1[0]) * (r1[3]-r1[1]))
    area2 = max(1, (r2[2]-r2[0]) * (r2[3]-r2[1]))
    return inter / min(area1, area2)

def _geometric_match(link: dict, elements: list, page_sizes: dict, threshold: float) -> dict | None:
    # Tente un match géométrique entre un lien et les éléments doctags.
    page_num = link["page_number"] - 1
    raw_rect = link["details"].get("from")
    if not raw_rect:
        return None

    pw, ph    = page_sizes.get(page_num, (595, 842))
    norm_rect = normalize_rect(raw_rect, pw, ph)

    best_score, best_elem = 0.0, None
    for elem in elements:
        if elem["page"] != page_num:
            continue
        # CORRECTION : exclure les éléments JSON du matching géométrique
        if elem.get("is_json"):
            continue
        score = overlap_ratio(norm_rect, (elem["x0"], elem["y0"], elem["x1"], elem["y1"]))
        if score > best_score:
            best_score, best_elem = score, elem

    if best_elem and best_score >= threshold:
        return {"element": best_elem, "score": round(best_score, 3)}
    return None

def _json_match(link: dict, elements: list) -> dict | None:
    # Tente un match textuel dans les blocs JSON sans coordonnées.
    page_num  = link["page_number"] - 1
    text_norm = normalize(link.get("text_display", ""))
    if not text_norm:
        return None

    for elem in elements:
        if not elem.get("is_json") or elem["page"] != page_num:
            continue
        try:
            obj = json.loads(elem["text"])
            for val in obj.values():
                if isinstance(val, str) and text_norm in normalize(val):
                    return {"element": elem, "score": 0.0}
        except Exception:
            pass
    return None

def _make_match(link: dict, element: dict, score: float, link_idx: int) -> dict:
    # Ajout de link_idx pour conserver l'ordre d'apparition dans le JSONL
    text = link.get("text", "")
    return {
        "uri": link["hyperlink"],
        "text": text,
        "text_display": link.get("text_display", text),
        "score": score,
        "element": element,
        "link_idx": link_idx,  # index dans le JSONL = ordre d'apparition dans le PDF
    }

def _text_match(link: dict, elements: list) -> dict | None:
    # Tente un match textuel dans les éléments NON-JSON.
    page_num = link["page_number"] - 1
    text_norm = normalize(link.get("text_display", ""))
    if not text_norm:
        return None

    for elem in elements:
        if elem.get("is_json") or elem["page"] != page_num:
            continue
        if text_norm in normalize(elem["text"]):
            return {"element": elem, "score": 0.0}
    return None

def match_links_to_elements(links: list, elements: list, page_sizes: dict, threshold: float = 0.1) -> list:
    matches = []
    matched_idxs = set()

    # Pass 1 : géométrique
    for idx, link in enumerate(links):
        result = _geometric_match(link, elements, page_sizes, threshold)
        if result:
            matches.append(_make_match(link, result["element"], result["score"], idx))  # idx
            matched_idxs.add(idx)
            print(f"Géo (score={result['score']:.2f}) p{link['page_number']} : '{link.get('text','')[:50]}'")
        else:
            raw_rect = link["details"].get("from")
            score_info = "(pas de rect)" if not raw_rect else "(score<threshold)"
            print(f"Pas de match géo {score_info} : '{link.get('text','')}' → {link['hyperlink']}")

    # Pass 1.5 : textuel sur éléments normaux
    for idx, link in enumerate(links):
        if idx in matched_idxs:
            continue
        result = _text_match(link, elements)
        if result:
            matches.append(_make_match(link, result["element"], 0.0, idx))  # idx
            matched_idxs.add(idx)
            print(f"Texte p{link['page_number']} : '{link.get('text_display','')[:50]}'")

    # Pass 2 : JSON fallback
    for idx, link in enumerate(links):
        if idx in matched_idxs:
            continue
        result = _json_match(link, elements)
        if result:
            matches.append(_make_match(link, result["element"], 0.0, idx))  # idx
            matched_idxs.add(idx)
            print(f"JSON  p{link['page_number']} : '{link.get('text_display','')[:50]}'")
        else:
            print(f"Aucun match : '{link.get('text_display','')}' → {link['hyperlink']}")

    return matches

def group_matches_by_element(matches: list) -> dict:
    elem_links = defaultdict(list)
    for match in matches:
        raw = match["element"]["raw"]
        elem_links[raw].append(match)
    # Trier chaque groupe par link_idx = ordre d'apparition dans le PDF
    for raw in elem_links:
        elem_links[raw].sort(key=lambda m: m["link_idx"])
    return elem_links

def inject_urls_in_jsonl_line(jsonl_line: str, link_matches: list) -> str:
    try:
        obj = json.loads(jsonl_line)
    except Exception as e:
        print(f"JSON invalide : {e}")
        return jsonl_line

    for m in link_matches:
        anchor = m.get("text_display") or m.get("text") or ""
        uri    = m.get("uri") or ""
        if not anchor or not uri:
            continue
        md_link = _md(anchor, uri)
        anchor_norm = normalize(anchor)
        pattern = _build_pattern(anchor) 
        for key in obj:
            if not isinstance(obj[key], str):
                continue
            if anchor_norm in normalize(obj[key]):
                obj[key] = pattern.sub(md_link, obj[key], count=1)
    return json.dumps(obj, ensure_ascii=False)

def _md(text: str, uri: str) -> str:
    # Construit un lien Markdown.
    return f"[{text}]({uri})"

def _is_full_element_match(elem_norm: str, text_norm: str, md: str) -> bool:
    # Vérifie si le texte du lien correspond exactement à l'élément entier (normalisé).
    # Gère aussi le cas où le markdown est déjà présent dans l'élément.
    if text_norm == elem_norm:
        return True
    # Cas : "texte - [texte](url)" ou "texte [texte](url)"
    if re.match(rf"^{re.escape(text_norm)}\s*[-–—]?\s*{re.escape(md)}$", elem_norm):
        return True
    if re.match(rf"^{re.escape(text_norm)}.*{re.escape(md)}$", elem_norm):
        return True
    return False

def _anchor_already_covered(new_text: str, anchor: str) -> bool:
    """Vérifie si l'ancre est déjà couverte par un lien markdown existant dans le texte."""
    anchor_norm = normalize(anchor)
    # Cherche tous les liens markdown déjà présents
    for md_text in re.findall(r'\[([^\]]+)\]\([^)]+\)', new_text):
        if anchor_norm in normalize(md_text):
            return True
    return False

def build_linked_text(elem_text: str, link_matches: list) -> str:
    """
    Remplace les ancres dans le texte dans l'ordre d'apparition des liens (link_idx).
    Utilise un curseur de position pour éviter de remplacer la mauvaise occurrence.
    """
    elem_norm = normalize(elem_text)
    new_text  = elem_text
    cursor    = 0

    # Cas 1 : un seul lien et correspond exactement à tout l'élément
    if len(link_matches) == 1:
        m      = link_matches[0]
        anchor = m.get("text_display") or m.get("text") or ""
        uri    = m.get("uri")
        if anchor and uri:
            md = _md(anchor, uri)
            if _is_full_element_match(elem_norm, normalize(anchor), md):
                return md

    # Cas 2 : remplacement séquentiel avec curseur
    for m in link_matches:
        anchor = m.get("text_display") or m.get("text") or ""
        uri    = m.get("uri")
        if not anchor or not uri:
            continue
        md      = _md(anchor, uri)
        pattern = _build_pattern(anchor)

        hit = pattern.search(new_text, cursor)
        if hit:
            s, e     = hit.span()
            new_text = new_text[:s] + md + new_text[e:]
            cursor   = s + len(md)
            print(f"  ✅ Ancre remplacée à pos {s} : '{anchor[:40]}'")
        else:
            # ✅ NOUVEAU : vérifie si l'ancre est déjà couverte par un markdown existant
            if _anchor_already_covered(new_text, anchor):
                print(f"  ⏭️  Ancre déjà couverte, ignorée : '{anchor[:40]}'")
                continue
            # Cas 3 : ancre non trouvée et non couverte → ajout à la fin
            new_text = new_text.rstrip() + md
            cursor   = len(new_text)
            print(f"  ⚠️  Ancre non trouvée après curseur, ajoutée en fin : '{anchor[:40]}'")

    return new_text

def _is_json(text: str) -> bool:
    try:
        return text.strip().startswith("{") and isinstance(json.loads(text.strip()), dict)
    except Exception:
        return False

def inject_links_in_doctags(content: str, matches: list) -> str:
    elem_links = group_matches_by_element(matches)

    for raw_tag, link_matches in elem_links.items():
        tag_name = link_matches[0]["element"]["tag"]
        text_orig = link_matches[0]["element"]["text"]
        stripped = text_orig.strip()

        if _is_json(stripped):
            print(f"Contenu JSONL détecté dans <{tag_name}>, injection dans les valeurs JSON")
            new_text = inject_urls_in_jsonl_line(stripped, link_matches)
        else:
            new_text = build_linked_text(text_orig, link_matches)

        # Remplacement dans le raw_tag
        if text_orig in raw_tag:
            new_raw = raw_tag.replace(text_orig, new_text, 1)
        else:
            pattern = re.compile(re.escape(text_orig), flags=re.IGNORECASE)
            new_raw = pattern.sub(new_text, raw_tag, count=1)

        content = content.replace(raw_tag, new_raw, 1)
        print(f"Injecté dans <{tag_name}> : {new_text[:100]}...")

    return content

if __name__ == "__main__":
    print("\nÉTAPE 1 — Chargement des liens JSONL")
    links = load_hyperlinks(hyperlinks_path)

    print("\nÉTAPE 2 — Tailles des pages PDF")
    page_sizes = get_page_sizes(pdf_path)
    for p, s in page_sizes.items():
        print(f"  Page {p+1} : {s[0]:.1f} x {s[1]:.1f} pts") # Affiche les tailles des pages pour debug

    print("\nÉTAPE 3 — Parsing des doctags")
    elements, content = parse_doctags(doctags_path)

    print("\nÉTAPE 4 — Matching liens ↔ doctags")
    matches = match_links_to_elements(links, elements, page_sizes, threshold=0.1)
    print(f"\n→ {len(matches)} match(s) trouvé(s)")

    print("\nÉTAPE 5 — Réinjection dans les doctags")
    enriched_content = inject_links_in_doctags(content, matches)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(enriched_content, encoding="utf-8")
    print(f"\nFichier enrichi sauvegardé : {output_path}")