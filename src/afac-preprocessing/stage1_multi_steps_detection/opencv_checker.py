from pathlib import Path
import fitz  # PyMuPDF
import cv2
import numpy as np
import re
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

DOC_NAME = os.environ.get("DOC_NAME", "") # Modifie le nom du PDF si nécessaire

project_root = Path(__file__).resolve().parent.parent
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
doctag_path = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags" 
output_dir = project_root / "data" / "output_files" / "stage1_test" / f"opencv_doctags_allpages_{DOC_NAME}" 
output_dir.mkdir(parents=True, exist_ok=True)
dpi = 300 # https://easyocr.org/en/help/easyocr-free-online-guide
# ici 300 car il faut que les coordonnées des doctags soient ajustées pour correspondre à la résolution de l'image générée, et 300 DPI est une résolution courante pour les PDF. 
# Si les doctags sont basés sur une normalisation différente (ex: 500), il faudra ajuster les coordonnées en conséquence.

norm_max = 500  
# Docling’s Internal Normalization
# Docling normalizes all box coordinates (x0, y0, x1, y1) to a fixed range for consistency, regardless of the actual DPI or pixel size of the source image.
# For most pipelines (especially with EasyOCR), Docling uses a normalization base of 500.
# That means:
# The top-left of the page is (0, 0)
# The bottom-right is (500, 500)
# All coordinates in the .doctags are scaled to fit this 500x500 grid.

# https://docling-project.github.io/docling/reference/pipeline_options/#docling.datamodel.pipeline_options.KserveV2OcrOptions.model_version

def parse_doctags_boxes(doctags_path):
    # Parse les .doctags pour extraire les boxes et leurs tags associés, organisés par page.
    boxes_by_page = {}
    current_page = 0
    with open(doctags_path, "r", encoding="utf-8") as f:
        for line in f:
            # Nettoie les balises globales
            line = line.replace("<doctag>", "").replace("</doctag>", "").strip()
            if not line:
                continue
            
            # Trouve toutes les balises ouvrantes <tag> (ignore les fermantes </tag>)
            for tag_match in re.finditer(r"<(?!/)(\w+)>", line):
                tag = tag_match.group(1)
                
                # Cherche les coordonnées immédiatement après cette balise
                rest_of_line = line[tag_match.end():]
                coords_match = re.match(r"<loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)><loc_(-?\d+)>", rest_of_line)
                
                if coords_match:
                    x0, y0, x1, y1 = map(int, coords_match.groups())
                    boxes_by_page.setdefault(current_page, []).append((x0, y0, x1, y1, tag))
            
            # Pagination : après traitement de toutes les balises de la ligne
            if "<page_footer>" in line:
                current_page += 1
    
    return boxes_by_page

boxes_by_page = parse_doctags_boxes(doctag_path)

#2. Pour chaque page, génère l'image et dessine les boxes
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
    # facteur de correction:
    # correction_factor = 2 # si norm_max est à 1000 
    for (x0n, y0n, x1n, y1n, tag) in boxes:
        # Convertit les coordonnées normalisées en coordonnées d'image, 
        # les coordonnées dans les doctags sont normalisées sur 500, 
        # donc on les convertit en pixels en fonction de la taille de l'image
        x0 = int(x0n / norm_max * img_w)
        y0 = int(y0n / norm_max * img_h)
        x1 = int(x1n / norm_max * img_w)
        y1 = int(y1n / norm_max * img_h)
        color = color_map.get(tag, (0, 255, 255)) # Jaune par défaut si tag inconnu
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        cv2.putText(img, tag, (x0, max(y0-5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    output_img = output_dir / f"page_{page_num+1}_doctags_boxes.png"
    cv2.imwrite(str(output_img), img)
    print(f"Image sauvegardée : {output_img}")