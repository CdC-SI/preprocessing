from pathlib import Path
import fitz  # PyMuPDF
import jsonlines  # pour sauvegarder les résultats dans un fichier JSONL
from dotenv import load_dotenv
import os
import re

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test" # Je suis sur l'.env.test qui est le même que le .env
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)

def is_external_link(uri):
    # On considère comme lien externe les liens commençant par http://, https:// ou mailto:
    return uri and uri.startswith(("http://", "https://", "mailto:"))

def geet_link_text(link, words):
    # "from" contient les coordonnées du rectangle du lien, 
    # on vérifie quels mots ont leur centre dans ce rectangle pour les associer au lien
    rect = link.get("from", None)
    if not rect:
        return "No text"
    
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words # w[0], w[1], w[2], w[3] sont les coordonnées du mot, w[4] est le texte du mot
        if rx0 <= (w[0]+w[2])/2 <= rx1 and ry0 <= (w[1]+w[3])/2 <= ry1 # on vérifie si le centre du mot est dans le rectangle du lien pour l'associer au lien
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
                "text": geet_link_text(link, words),
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
hyperlink_data_path.parent.mkdir(parents=True, exist_ok=True)  # <-- correction ici

# Extract and debug hyperlinks
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