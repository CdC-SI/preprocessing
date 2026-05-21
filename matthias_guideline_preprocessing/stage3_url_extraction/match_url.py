from pathlib import Path
import re
import json
import jsonlines
import fitz  # PyMuPDF

# Root
DOC_NAME = "Adhésion traitement" # CHANGER SELON LES TESTS
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

def parse_doctags(doctags_path: Path) -> list:
    # Parse toutes les balises avec coordonnées.
    # Retourne une liste d'éléments :
    # {page, tag, x0, y0, x1, y1, text, raw_tag, raw_start_pos}

    content = Path(doctags_path).read_text(encoding="utf-8")
    elements = []
    current_page = 0

    for match in re.finditer(
        r'<(?!/)(?!doctag)(\w+)>'
        r'<loc_(\d+)><loc_(\d+)><loc_(\d+)><loc_(\d+)>'
        r'(.*?)'
        r'(?=<(?!loc_)\w|$)',
        content,
        re.DOTALL
    ):
        tag  = match.group(1)
        x0, y0, x1, y1 = int(match.group(2)), int(match.group(3)), \
                          int(match.group(4)), int(match.group(5))
        text = re.sub(r'<[^>]+>', '', match.group(6)).strip()

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

def inject_links_in_doctags(content: str, matches: list) -> str:
    from collections import defaultdict
    elem_links = defaultdict(list)
    for match in matches:
        raw = match["element"]["raw"]
        elem_links[raw].append(match)

    for raw_tag, link_matches in elem_links.items():
        tag_name  = link_matches[0]["element"]["tag"]
        close_tag = f"</{tag_name}>" # Vérifier la basiese du tag pour éviter les erreurs
        text_orig = link_matches[0]["element"]["text"]

        # On travaille sur une copie du texte original
        new_text = text_orig
        already_linked = set()

        for m in link_matches:
            if not m['text']:
                continue
            # Pour éviter de linker plusieurs fois le même texte
            if m['text'] in already_linked:
                continue
            already_linked.add(m['text'])
            # Remplace la première occurrence du texte du lien par le markdown
            if m['text'] in new_text:
                new_text = new_text.replace(
                    m['text'],
                    f"[{m['text']}]({m['uri']})",
                    1
                )
            else:
                # Si le texte n'est pas trouvé, ajoute à la fin
                new_text += f" [{m['text']}]({m['uri']})"

        # Si aucun texte de lien n'a été trouvé, ajoute tous les liens à la fin
        if not already_linked and link_matches:
            for m in link_matches:
                new_text += f" [{m['uri']}]"

        # Remplace dans le raw_tag
        new_raw = raw_tag.replace(text_orig, new_text, 1)
        content = content.replace(raw_tag, new_raw, 1)
        print(f"Injecté dans <{tag_name}> : {new_text}")

    return content

if __name__ == "__main__":
    print("=" * 60)
    print("ÉTAPE 1 — Chargement des liens JSONL")
    print("=" * 60)
    links = load_hyperlinks(hyperlinks_path)

    print("\n" + "=" * 60)
    print("ÉTAPE 2 — Tailles des pages PDF")
    print("=" * 60)
    page_sizes = get_page_sizes(pdf_path)
    for p, s in page_sizes.items():
        print(f"  Page {p+1} : {s[0]:.1f} x {s[1]:.1f} pts")

    print("\n" + "=" * 60)
    print("ÉTAPE 3 — Parsing des doctags")
    print("=" * 60)
    elements, content = parse_doctags(doctags_path)

    print("\n" + "=" * 60)
    print("ÉTAPE 4 — Matching liens ↔ doctags")
    print("=" * 60)
    matches = match_links_to_elements(links, elements, page_sizes, threshold=0.1)
    print(f"\n→ {len(matches)} match(s) trouvé(s)")

    print("\n" + "=" * 60)
    print("ÉTAPE 5 — Réinjection dans les doctags")
    print("=" * 60)
    enriched_content = inject_links_in_doctags(content, matches)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(enriched_content, encoding="utf-8")
    print(f"\nFichier enrichi sauvegardé : {output_path}")