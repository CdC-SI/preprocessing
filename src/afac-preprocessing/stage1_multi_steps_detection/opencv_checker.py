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
config = load_vlm_config()

DOC_NAME = os.environ.get("DOC_NAME", "")

project_root = Path(__file__).resolve().parent.parent
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
doctag_path = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags" 
output_dir = project_root / "data" / "output_files" / "stage1_test" / f"opencv_doctags_allpages_{DOC_NAME}" 
output_dir.mkdir(parents=True, exist_ok=True)

dpi = 300 # https://easyocr.org/en/help/easyocr-free-online-guide
# ici 300 car il faut que les coordonnées des doctags soient ajustées pour correspondre à la résolution de l'image générée, et 300 DPI est une résolution courante pour les PDF. 
# Si les doctags sont basés sur une normalisation différente (ex: 500), il faudra ajuster les coordonnées en conséquence.

norm_max = 500  
# Normalisation interne de Docling
# Docling normalise toutes les coordonnées des boîtes (x0, y0, x1, y1) dans une plage fixe pour assurer la cohérence, indépendamment de la résolution (DPI) ou de la taille en pixels de l'image source.
# Pour la plupart des pipelines (notamment avec EasyOCR), Docling utilise une base de normalisation de 500.
# Cela signifie :
# Le coin supérieur gauche de la page est (0, 0)
# Le coin inférieur droit est (500, 500)
# Toutes les coordonnées dans les balises .doctags sont mises à l'échelle pour s'adapter à cette grille 500x500.

# https://docling-project.github.io/docling/reference/pipeline_options/#docling.datamodel.pipeline_options.KserveV2OcrOptions.model_version


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
            line = line.replace("<doctag>", "").replace("</doctag>", "").strip() # Nettoie les balises globales
            if not line:
                continue
            
            # Trouve toutes les balises ouvrantes <tag> (ignore les fermantes </tag>)
            for tag_match in re.finditer(r"<(?!/)(\w+)>", line): # <(?!/) : match les balises qui ne sont pas suivies de /, (\w+) : capture le nom du tag
                tag = tag_match.group(1)
                
                # Cherche les coordonnées immédiatement après cette balise
                rest_of_line = line[tag_match.end():]
                coords_match = re.match(r"<loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)>", rest_of_line) # <loc_x0><loc_y0><loc_x1><loc_y1> avec des nombres entiers (possiblement négatifs)
                
                if coords_match:
                    x0, y0, x1, y1 = map(int, coords_match.groups()) # On retire la virgule et convertit en int
                    boxes_by_page.setdefault(current_page, []).append((x0, y0, x1, y1, tag))
            
            # Détection de la pagination : incrémente le numéro de page à chaque occurrence de <page_footer>
            if "<page_footer>" in line:
                current_page += 1
    
    return boxes_by_page

boxes_by_page = parse_doctags_boxes(doctag_path)

# Création de box de color pour chaque tag
# Pour chaque page, génère l'image et dessine les boxes
doc = fitz.open(pdf_path)
num_pages = len(doc)

color_map = {
    "picture": (0, 0, 255),                  # Rouge
    "text": (0, 255, 0),                     # Vert
    "table": (255, 0, 0),                    # Bleu
    "page_header": (255, 255, 0),            # Cyan
    "page_footer": (255, 0, 255),            # Magenta
    "section_header_level_1": (0, 128, 255), # Orange
    "section_header_level_2": (128, 128, 0), # Olive
    "section_header_level_3": (0, 128, 128), # Bleu-vert
    "otsl": (128, 0, 255),                   # Violet
    "unordered_list": (0, 0, 0),             # Noir
    "ordered_list": (128, 128, 255),         # Bleu clair
    "list_item": (255, 128, 0),              # Orange foncé
    "table_cell": (128, 0, 0),               # Bordeaux
    "table_row": (0, 128, 0),                # Vert foncé
    "table_header": (0, 0, 128),             # Bleu foncé
    "caption": (255, 192, 203),              # Rose
    "footnote": (128, 128, 128),             # Gris
    "reference": (255, 215, 0),              # Or
    "figure": (255, 140, 0),                 # Orange vif
    "equation": (0, 255, 127),               # Vert printemps
    "highlight": (255, 255, 255),            # Blanc
    "link": (0, 0, 0),                       # Noir
    "fcel": (128, 0, 255),                   # Violet
    "ched": (0, 200, 200),                   # Cyan foncé 
    "ecel": (200, 200, 0),                   # Jaune foncé
    "lcel": (200, 0, 200),                   # Violet foncé
    "rhed": (0, 100, 255),                   # Bleu vif
    "nl": (150, 150, 150),                   # Gris clair
}

# boucle for pour parcourir les pages du PDF, générer l'image et dessiner les boxes
for page_num in range(num_pages):
    page = doc[page_num]
    pix = page.get_pixmap(dpi=dpi)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
    img = img.copy()
    img_h, img_w = img.shape[:2]
    pdf_width = page.rect.width
    pdf_height = page.rect.height

    boxes = boxes_by_page.get(page_num, [])
    for (x0n, y0n, x1n, y1n, tag) in boxes:
    # Convertit les coordonnées normalisées (0-500) en coordonnées d'image (0-img_w ou 0-img_h)
        x0 = int(x0n / norm_max * img_w) 
        y0 = int(y0n / norm_max * img_h)
        x1 = int(x1n / norm_max * img_w)
        y1 = int(y1n / norm_max * img_h)
        color = color_map.get(tag, (0, 255, 255)) # Jaune par défaut si tag inconnu
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        cv2.putText(img, tag, (x0, max(y0-5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    output_img = output_dir / f"page_{page_num+1}_doctags_boxes.png"
    cv2.imwrite(str(output_img), img) # Sauvegarde l'image avec les boxes dessinées
    print(f"Image sauvegardée : {output_img}")