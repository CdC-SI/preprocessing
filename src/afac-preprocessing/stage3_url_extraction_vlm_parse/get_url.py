from pathlib import Path
import fitz  # PyMuPDF
import jsonlines  # pour sauvegarder les résultats dans un fichier JSONL
import os
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

def is_external_link(uri):
    # On considère comme lien externe les liens commençant par http://, https:// ou mailto:
    return uri and uri.startswith(("http://", "https://", "mailto:"))

def get_link_text(link, words):
    # on vérifie quels mots ont leur centre dans ce rectangle pour les associer au lien
    rect = link.get("from", None) # "from" contient les coordonnées du rectangle du lien (cf. voir jsonl de sortie stage 3)
    if not rect:
        return "No text"
    
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words # w[0], w[1], w[2], w[3] sont les coordonnées du mot, w[4] est le texte du mot
        if rx0 <= (w[0] + w[2]) /2 <= rx1 and ry0 <= (w[1] + w[3]) /2 <= ry1 # on vérifie si le centre du mot est dans le rectangle du lien pour l'associer au lien.
    ]
    return " ".join(link_words).strip() if link_words else "No text"

def serialize_link(link):
    # fitz.Rect n'est pas directement sérialisable en JSON, on le convertit en liste de coordonnées
    link_serializable = link.copy()
    if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect):
        link_serializable["from"] = list(link_serializable["from"])
    return link_serializable

def extract_url_links(pdf_path):
    # Ouvre le PDF et extrait les liens URI de chaque page, 
    # en associant le texte des mots qui se trouvent dans le rectangle du lien
    doc = fitz.open(pdf_path)
    results = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        links = page.get_links()
        words = page.get_text("words")

        for link in links:
            uri = link.get("uri")
            if not is_external_link(uri):
                continue

            link_details = {
                "page_number": page_num + 1,
                "text": get_link_text(link, words),
                "hyperlink": uri,
                "type": "URI",
                "details": serialize_link(link),
            }
            results.append(link_details)
    return results

# Root
DOC_NAME = os.environ.get("DOC_NAME", "") # CHANGER SELON LES TESTS
project_root = Path(__file__).resolve().parent.parent
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
hyperlink_data_path = project_root / "data" / "output_files" / "stage3_test" / DOC_NAME / f"hyperlinks_data_{DOC_NAME}.json"
hyperlink_data_path.parent.mkdir(parents=True, exist_ok=True) # créer le dossier de sortie s'il n'existe pas

# Extrait et debug les liens hypertextes
hyperlinks_data = extract_url_links(pdf_path)
for data in hyperlinks_data:
    print(f"Page number: {data['page_number']}")
    print(f"Text: {data['text']}")
    print(f"Type: {data['type']}")
    print(f"Hyperlink: {data['hyperlink']}")
    print(f"Details: {data['details']}")
    print("\n")

with jsonlines.open(hyperlink_data_path.with_suffix('.jsonl'), mode='w') as writer:
    for item in hyperlinks_data:
        writer.write(item)
print(f"Hyperlinks saved to: {hyperlink_data_path.with_suffix('.jsonl')}")