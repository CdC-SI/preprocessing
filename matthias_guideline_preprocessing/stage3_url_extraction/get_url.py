from pathlib import Path
import fitz  # PyMuPDF
import jsonlines  # pour sauvegarder les résultats dans un fichier JSONL

def extract_url_links(pdf_path):
    doc = fitz.open(pdf_path)
    results = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        links = page.get_links()
        words = page.get_text("words")  # liste de (x0, y0, x1, y1, "mot", block_no, line_no, word_no)

        for link in links:
            link_serializable = link.copy()
            if "from" in link_serializable and isinstance(link_serializable["from"], fitz.Rect): # "from" contient les coordonnées du lien
                link_serializable["from"] = list(link_serializable["from"])

            uri = link.get("uri")
            rect = link.get("from", None)
            link_text = ""
            if rect:
                rx0, ry0, rx1, ry1 = rect
                # Prend les mots dont le centre est dans le rectangle du lien
                link_words = [
                    w[4] for w in words # w[0]=x0, w[1]=y0, w[2]=x1, w[3]=y1, w[4]=mot
                    if rx0 <= (w[0]+w[2])/2 <= rx1 and ry0 <= (w[1]+w[3])/2 <= ry1 # vérifie si le centre du mot est dans le rectangle du lien
                ]
                link_text = " ".join(link_words).strip() if link_words else "No text"

            link_details = {
                "page_number": page_num + 1,
                "text": link_text,
                "hyperlink": uri if uri else None,
                "type": "URI" if uri else link.get("type", "Unknown"),
                "details": link_serializable,
            }
            results.append(link_details)
    return results

# Root
DOC_NAME = "Adhésion traitement" # CHANGER SELON LES TESTS
project_root = Path(__file__).resolve().parent.parent
pdf_path = project_root / "data" / "input_files" / f"{DOC_NAME}.pdf"
hyperlink_data_path = project_root / "data" / "output_files" / "stage3_test" / DOC_NAME /f"hyperlinks_data_{DOC_NAME}.json"
hyperlink_data_path.mkdir(parents=True, exist_ok=True)
# Extract and debug hyperlinks
hyperlinks_data = extract_url_links(pdf_path)
for data in hyperlinks_data:
    print(f"Page number: {data['page_number']}")
    print(f"Text: {data['text']}")
    print(f"Type: {data['type']}")
    print(f"Hyperlink: {data['hyperlink']}")
    print(f"Details: {data['details']}")
    print("-" * 50)

with jsonlines.open(hyperlink_data_path.with_suffix('.jsonl'), mode='w') as writer:
    for item in hyperlinks_data:
        writer.write(item)
print(f"Hyperlinks saved to: {hyperlink_data_path.with_suffix('.jsonl')}")