"""
Stage 1 - Multi-étapes de détection : Pipeline de conversion de documents avec Docling
Script 2 : opencv_checker.py

Après l'extraction des doctags dans le script précédent,
celui-ci vérifie visuellement la correspondance entre les doctags extraits et les zones du document source.
Les images générées permettent de valider que les doctags sont correctement positionnés avant les étapes d'extraction avancées.
Ces informations ne sont pas utilisées dans les étapes suivantes du pipeline — elles servent uniquement à la validation visuelle.
"""
from pathlib import Path
import fitz  # PyMuPDF
import cv2
import numpy as np
import re
import os
import sys

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config

# DPI 300 : résolution courante pour PDF, les coordonnées doctags sont ajustées en conséquence
DPI = 300

# Docling normalise toutes les coordonnées dans une grille 500x500 (coin supérieur gauche = (0,0), inférieur droit = (500,500))
# https://github.com/docling-project/docling/discussions/354
NORM_MAX = 500

COLOR_MAP = {
    "picture": (0, 0, 255),
    "text": (0, 255, 0),
    "table": (255, 0, 0),
    "page_header": (255, 255, 0),
    "page_footer": (255, 0, 255),
    "section_header_level_1": (0, 128, 255),
    "section_header_level_2": (128, 128, 0),
    "section_header_level_3": (0, 128, 128),
    "otsl": (128, 0, 255),
    "unordered_list": (0, 0, 0),
    "ordered_list": (128, 128, 255),
    "list_item": (255, 128, 0),
    "table_cell": (128, 0, 0),
    "table_row": (0, 128, 0),
    "table_header": (0, 0, 128),
    "caption": (255, 192, 203),
    "footnote": (128, 128, 128),
    "reference": (255, 215, 0),
    "figure": (255, 140, 0),
    "equation": (0, 255, 127),
    "highlight": (255, 255, 255),
    "link": (0, 0, 0),
    "fcel": (128, 0, 255),
    "ched": (0, 200, 200),
    "ecel": (200, 200, 0),
    "lcel": (200, 0, 200),
    "rhed": (0, 100, 255),
    "nl": (150, 150, 150),
}


def parse_doctags_boxes(doctags_path) -> dict:
    """
    Docstring for parse_doctags_boxes
    - Parse les .doctags pour extraire les boxes et leurs tags associés, organisés par page.

    :param doctags_path: Description
    :return: Description
    :rtype: dict
    """
    boxes_by_page = {} # Dictionnaire : { page_num: [(x0, y0, x1, y1, tag), ...], ... }
    current_page = 0
    with open(doctags_path, "r", encoding="utf-8") as f: # "r" pour lecture, "utf-8" pour s'assurer de lire correctement les caractères spéciaux
        for line in f:
            line = line.replace("<doctag>", "").replace("</doctag>", "").strip() # Nettoie les balises globales car pas de loc
            if not line:
                continue
            
            # Trouve toutes les balises ouvrantes <tag> (ignore les fermantes </tag> car elle n'ont pas de coordonnées)
            for tag_match in re.finditer(r"<(?!/)(\w+)>", line): # <(?!/) : match les balises qui ne sont pas suivies de /, (\w+) : capture le nom du tag
                tag = tag_match.group(1) # Récupère le nom du tag, groupe 1 de la regex ex : <picture> -> "picture"
                
                # Cherche les coordonnées immédiatement après cette balise
                rest_of_line = line[tag_match.end():]  # start à la fin de la balise trouvée
                coords_match = re.match(r"<loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)>", rest_of_line) # <loc_x0><loc_y0><loc_x1><loc_y1> avec des nombres entiers (possiblement négatifs)
                
                if coords_match:
                    x0, y0, x1, y1 = map(int, coords_match.groups()) # On retire la virgule et convertit en int
                    boxes_by_page.setdefault(current_page, []).append((x0, y0, x1, y1, tag))
            
            # Détection de la pagination : incrémente le numéro de page à chaque occurrence de <page_footer>
            if "<page_footer>" in line:
                current_page += 1

    return boxes_by_page


def main():
    load_vlm_config()
    doc_name = os.environ.get("DOC_NAME", "")

    pdf_path = PROJECT_ROOT / "data" / "input_files" / f"{doc_name}.pdf"
    doctag_path = PROJECT_ROOT / "data" / "output_files" / "stage1_test" / doc_name / f"{doc_name}.doctags"
    output_dir = PROJECT_ROOT / "data" / "output_files" / "stage1_test" / f"opencv_doctags_allpages_{doc_name}"
    output_dir.mkdir(parents=True, exist_ok=True)

    boxes_by_page = parse_doctags_boxes(doctag_path)

    doc = fitz.open(pdf_path)
    for page_num in range(len(doc)):
        page = doc[page_num]
        pix = page.get_pixmap(dpi=DPI)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
        img = img.copy()
        img_h, img_w = img.shape[:2]

        for (x0n, y0n, x1n, y1n, tag) in boxes_by_page.get(page_num, []):
            x0 = int(x0n / NORM_MAX * img_w)
            y0 = int(y0n / NORM_MAX * img_h)
            x1 = int(x1n / NORM_MAX * img_w)
            y1 = int(y1n / NORM_MAX * img_h)
            color = COLOR_MAP.get(tag, (0, 255, 255))
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
            cv2.putText(img, tag, (x0, max(y0 - 5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

        output_img = output_dir / f"page_{page_num + 1}_doctags_boxes.png"
        cv2.imwrite(str(output_img), img)
        print(f"Image sauvegardée : {output_img}")


if __name__ == "__main__":
    main()