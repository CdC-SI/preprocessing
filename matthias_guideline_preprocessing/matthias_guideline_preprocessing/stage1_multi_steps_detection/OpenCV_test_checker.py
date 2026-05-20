from pathlib import Path
import fitz  # PyMuPDF
import cv2
import numpy as np
import re

# Root
DOC_NAME = "Demande de justificatifs" # Modifie le nom du PDF si nécessaire
project_root = Path(__file__).resolve().parent.parent # Récupère le dossier racine du projet ici matthias_guideline_preprocessing (Chemin absolu)
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
doctag_path = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags" 
output_dir = project_root / "data" / "output_files" / "stage1_test" / f"opencv_doctags_allpages_{DOC_NAME}" 
output_dir.mkdir(parents=True, exist_ok=True)
dpi = 300
norm_max = 500  # Docling normalise sur 500 avec easyOCR et pas 1000 DPI

# 1. Parse les boxes du .doctags par page
def parse_doctags_boxes(doctags_path):
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
    "picture": (0, 0, 255),      # Rouge
    "text": (0, 255, 0),         # Vert
    "table": (255, 0, 0),        # Bleu
    "page_header": (255, 255, 0),# Cyan
    "page_footer": (255, 0, 255),# Magenta
    "section_header_level_1": (0, 128, 255), # Orange
    "section_header_level_2": (128, 128, 0), # Olive
    "section_header_level_3": (0, 128, 128), # Bleu-vert
    "otsl": (128, 0, 255),       # Violet
    "unordered_list": (0, 0, 0), # Noir
    "ordered_list": (128, 128, 255), # Bleu clair
    "list_item": (255, 128, 0),  # Orange foncé
    "table_cell": (128, 0, 0),   # Bordeaux
    "table_row": (0, 128, 0),    # Vert foncé
    "table_header": (0, 0, 128), # Bleu foncé
    "caption": (255, 192, 203),  # Rose
    "footnote": (128, 128, 128), # Gris
    "reference": (255, 215, 0),  # Or
    "figure": (255, 140, 0),     # Orange vif
    "equation": (0, 255, 127),   # Vert printemps
    "highlight": (255, 255, 255),# Blanc
    "link": (0, 0, 0),           # Noir
    "fcel": (128, 0, 255),       # Violet
    "ched": (0, 200, 200),       # Cyan foncé 
    "ecel": (200, 200, 0),       # Jaune foncé
    "lcel": (200, 0, 200),       # Violet foncé
    "rhed": (0, 100, 255),       # Bleu vif
    "nl": (150, 150, 150),       # Gris clair
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
        # x0n *= correction_factor # si norm_max est à 1000 
        # y0n *= correction_factor # si norm_max est à 1000 
        # x1n *= correction_factor # si norm_max est à 1000 
        # y1n *= correction_factor # si norm_max est à 1000 

        # Dénormalisation sur la taille du PDF (ou de l'image, ici c'est équivalent car on utilise le rendu PDF)
        x0 = int(x0n / norm_max * img_w)
        y0 = int(y0n / norm_max * img_h)
        x1 = int(x1n / norm_max * img_w)
        y1 = int(y1n / norm_max * img_h)
        color = color_map.get(tag, (0, 255, 255))
        cv2.rectangle(img, (x0, y0), (x1, y1), color, 2)
        cv2.putText(img, tag, (x0, max(y0-5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)

    output_img = output_dir / f"page_{page_num+1}_doctags_boxes.png"
    cv2.imwrite(str(output_img), img)
    print(f"Image sauvegardée : {output_img}")