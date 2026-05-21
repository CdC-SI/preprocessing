from pathlib import Path
import fitz  # PyMuPDF
import cv2
import numpy as np

# Root
DOC_NAME = "Confirmer l'adhésion.pdf" # Modifie le nom du PDF si nécessaire
project_root = Path(__file__).resolve().parent.parent # Récupère le dossier racine du projet ici matthias_guideline_preprocessing (Chemin absolu)
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
doctag_path = project_root / "data" / "output_files" / "stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags" 
output_dir = project_root / "data" / "output_files" / "stage1_test" / f"opencv_doctags_allpages_{DOC_NAME}" 
output_dir.mkdir(parents=True, exist_ok=True)
dpi = 300
norm_max = 500  # Docling normalise sur 500 avec easyOCR et pas 1000 DPI

# Coordonnées normalisées à tester 
x0n, y0n, x1n, y1n = 281, 33, 393, 50  # À modifier selon le test

# Génère l'image de la première page
doc = fitz.open(str(pdf_path))
page = doc[0]
pix = page.get_pixmap(dpi=dpi)
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
if img.shape[2] == 4:
    img = cv2.cvtColor(img, cv2.COLOR_RGBA2RGB)
img = img.copy()
img_h, img_w = img.shape[:2]

# Dénormalisation
x0 = int(x0n / norm_max * img_w)
y0 = int(y0n / norm_max * img_h)
x1 = int(x1n / norm_max * img_w)
y1 = int(y1n / norm_max * img_h)

# Dessine la box
cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 3)
cv2.putText(img, "manual_box", (x0, max(y0-10, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,255), 2, cv2.LINE_AA)

# Sauvegarde
output_img = output_dir / "manual_box_check.png"
cv2.imwrite(str(output_img), img)
print(f"Image sauvegardée : {output_img}")