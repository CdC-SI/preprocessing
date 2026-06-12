"""
Stage 3 - Script d'extraction des liens hypertextes (URL, mailto) avec PyMuPDF
Script 1 : get_url.py

Ce script utilise la bibliothèque PyMuPDF pour ouvrir le PDF source et extraire les liens hypertextes de chaque page.
Il vérifie si les liens sont des liens externes (commençant par http://, https:// ou mailto:), 
puis associe le texte des mots qui se trouvent dans le rectangle du lien pour fournir un contexte textuel à chaque lien extrait.
"""
from pathlib import Path
import fitz  # PyMuPDF
import jsonlines  # pour sauvegarder les résultats dans un fichier JSONL
import os
import sys

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
load_vlm_config()


def is_external_link(uri) -> bool:
    """
    Docstring for is_external_link
    - On considère comme lien externe les liens commençant par http://, https:// ou mailto:

    :param uri: Description
    """
    return uri and uri.startswith(("http://", "https://", "mailto:")) # retourne true si uri n'est pas None et commence par http://, https:// ou mailto:


def get_link_text(link, words) -> str:
    """
    Docstring for get_link_text
    - On vérifie quels mots ont leur centre dans ce rectangle pour les associer au lien

    :param link: Description
    :param words: Description
    """
    rect = link.get("from", None) # "from" contient les coordonnées du rectangle du lien (cf. voir jsonl de sortie stage 3)
    if not rect:
        return "No text"
    
    rx0, ry0, rx1, ry1 = rect
    link_words = [
        w[4] for w in words # w[0], w[1], w[2], w[3] sont les coordonnées du mot, w[4] est le texte du mot
        if rx0 <= (w[0] + w[2]) / 2 <= rx1 and ry0 <= (w[1] + w[3]) / 2 <= ry1 # on vérifie si le centre du mot est dans le rectangle du lien pour l'associer au lien.
    ]
    return " ".join(link_words).strip() if link_words else "No text"


def serialize_link(link) -> dict:
    """
    Docstring for serialize_link
    - fitz.Rect n'est pas directement sérialisable en JSON, on le convertit en liste de coordonnées
    - On retourne une copie du dict du lien avec "from" converti en liste si c'est un fitz.Rect, 
    pour pouvoir le sauvegarder dans un format JSONL.
    
    :param link: Description
    """
    link_serializable = link.copy()
    if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect):
        link_serializable["from"] = list(link_serializable["from"])
    return link_serializable


def extract_url_links(pdf_path) -> list[dict]:
    """
    Docstring for extract_url_links
    - Ouvre le PDF et extrait les liens URI de chaque page, 
    en associant le texte des mots qui se trouvent dans le rectangle du lien

    :param pdf_path: Description
    """
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

def main():
    # Root
    DOC_NAME = os.environ.get("DOC_NAME", "")
    pdf_path = PROJECT_ROOT / "data" / "input_files" / f"{DOC_NAME}.pdf"
    hyperlink_data_path = PROJECT_ROOT / "data" / "output_files" / "stage3_test" / DOC_NAME / f"hyperlinks_data_{DOC_NAME}.jsonl"
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

    with jsonlines.open(hyperlink_data_path, mode='w') as writer:
        for item in hyperlinks_data:
            writer.write(item)
    print(f"Hyperlinks saved to: {hyperlink_data_path}")


if __name__ == "__main__":
    main()