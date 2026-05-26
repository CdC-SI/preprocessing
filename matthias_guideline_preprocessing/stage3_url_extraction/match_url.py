from pathlib import Path
import re
import json
import jsonlines
import fitz  # PyMuPDF
from dotenv import load_dotenv
import os
from collections import defaultdict
from typing import Tuple

# Chargement de .env.test
load_dotenv()
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

# Charge les liens extraits du PDF (get_url.py)
def load_hyperlinks(jsonl_path: Path) -> list:
    links = []
    with jsonlines.open(jsonl_path) as reader:
        for item in reader:
            if item.get("type") == "URI" and item.get("hyperlink"):
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
        })

        if tag == "page_footer":
            current_page += 1

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

def match_links_to_elements(links: list, elements: list, page_sizes: dict, threshold: float = 0.1) -> list:
    # Pour chaque lien, trouve l'élément doctags correspondant.
    # Un élément peut recevoir PLUSIEURS liens si le paragraphe en contient plusieurs.
    
    matches = []

    for link in links:
        page_num = link["page_number"] - 1  # 0-based
        raw_rect = link["details"].get("from")
        uri = link["hyperlink"]
        text = link.get("text", "")

        if not raw_rect:
            print(f"Pas de rect pour {uri}")
            continue

        pw, ph = page_sizes[page_num]
        norm_rect = normalize_rect(raw_rect, pw, ph)

        best_score = 0.0
        best_element = None

        for elem in elements:
            if elem["page"] != page_num:
                continue
            er = (elem["x0"], elem["y0"], elem["x1"], elem["y1"])
            score = overlap_ratio(norm_rect, er)
            if score > best_score:
                best_score = score
                best_element = elem

        if best_element and best_score >= threshold:
            matches.append({
                "uri": uri,
                "text": text,
                "score": round(best_score, 3),
                "element": best_element,
            })
            print(f"Match (score={best_score:.2f}) page {page_num+1}")
            print(f"Texte lien : '{text}'")
            print(f"Texte elem : '{best_element['text'][:60]}'")
            print(f"URL : {uri}")
        else:
            print(f"Pas de match pour '{text}' → {uri} (score={best_score:.2f})")

    return matches

def group_matches_by_element(matches: list) -> dict:
    # Regroupe les liens par élément doctags (basé sur le raw_tag)
    elem_links = defaultdict(list)
    for match in matches:
        raw = match["element"]["raw"]
        elem_links[raw].append(match)
    return elem_links

def normalize(text):
    # Remplace tous les tirets par un tiret standard, retire les espaces multiples et les espaces insécables
    text = re.sub(r'[\-–—]', '-', text)
    text = text.replace('\u00A0', ' ')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def build_linked_text(elem_text: str, link_matches: list) -> str:
    def markdown_link(text, uri):
        return f"[{text}]({uri})"

    elem_norm = normalize(elem_text)
    for m in link_matches:
        if not m['text']:
            continue
        text_norm = normalize(m['text'])
        md = markdown_link(m['text'], m['uri'])
        # Cas 1 : texte du lien = texte de l'élément (normalisé)
        if text_norm == elem_norm:
            return md
        # Cas 2 : l'élément commence par le texte du lien (normalisé), suivi d'un séparateur et du markdown
        pattern = rf"^{re.escape(text_norm)}\s*-\s*{re.escape(md)}$"
        if re.match(pattern, elem_norm):
            return md
        # Cas 3 : l'élément contient le texte du lien suivi du markdown (doublon)
        pattern2 = rf"^{re.escape(text_norm)}.*{re.escape(md)}$"
        if re.match(pattern2, elem_norm):
            return md

    # Sinon, comportement standard
    new_text = elem_text
    already_linked = set()
    links_to_add = []

    for m in link_matches:
        text = m.get('text')
        uri = m.get('uri')
        if not text or text in already_linked:
            continue
        already_linked.add(text)
        text_norm = normalize(text)
        new_text_norm = normalize(new_text)
        if text_norm in new_text_norm:
            # Remplacement best-effort sur le texte original
            pattern = re.compile(re.escape(text), re.IGNORECASE)
            new_text = pattern.sub(markdown_link(text, uri), new_text, count=1)
        else:
            links_to_add.append(markdown_link(text, uri))

    if not already_linked and link_matches:
        for m in link_matches:
            uri = m.get('uri')
            if uri:
                links_to_add.append(f"[{uri}]")

    if links_to_add:
        new_text = new_text.rstrip() + " " + " ".join(links_to_add)

    return new_text.strip()

def inject_links_in_doctags(content: str, matches: list) -> str:
    # Injecte les liens enrichis dans le contenu du doctags, 
    # en remplaçant le texte original de chaque élément par le texte enrichi.
    elem_links = group_matches_by_element(matches)

    for raw_tag, link_matches in elem_links.items():
        tag_name  = link_matches[0]["element"]["tag"]
        text_orig = link_matches[0]["element"]["text"]
        new_text = build_linked_text(text_orig, link_matches)
        new_raw = raw_tag.replace(text_orig, new_text, 1)
        content = content.replace(raw_tag, new_raw, 1)
        print(f"Injecté dans <{tag_name}> : {new_text}")

    return content

if __name__ == "__main__":
    print("ÉTAPE 1 — Chargement des liens JSONL")
    links = load_hyperlinks(hyperlinks_path)

    page_sizes = get_page_sizes(pdf_path)
    for p, s in page_sizes.items():
        print(f"  Page {p+1} : {s[0]:.1f} x {s[1]:.1f} pts") # Affiche les tailles des pages pour debug

    print("ÉTAPE 3 — Parsing des doctags")
    elements, content = parse_doctags(doctags_path)

    print("ÉTAPE 4 — Matching liens ↔ doctags")
    matches = match_links_to_elements(links, elements, page_sizes, threshold=0.1)
    print(f"\n→ {len(matches)} match(s) trouvé(s)")

    print("ÉTAPE 5 — Réinjection dans les doctags")
    enriched_content = inject_links_in_doctags(content, matches)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(enriched_content, encoding="utf-8")
    print(f"\nFichier enrichi sauvegardé : {output_path}")