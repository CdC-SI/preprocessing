from pathlib import Path
import re
import os
import sys

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()


def extract_y0(line: str) -> int | None:
    # Extrait le y0 (2ème <loc_N>) d'une ligne. Retourne None si absent.
    m = re.search(r"<loc_\d+><loc_(\d+)>", line) # Explication "<loc_\d+><loc_(\d+)>" : on cherche une séquence de deux balises <loc_N><loc_M> où N et M sont des nombres. On capture M (y0) pour l'extraire. Si la ligne contient ce pattern, on retourne y0, sinon None.
    return int(m.group(1)) if m else None

def merge_closing_tags(lines: list[str]) -> list[str]:
    # Fusionne les balises fermantes seules sur une ligne avec la ligne précédente.
    # Ex: </unordered_list> seul → collé à la fin de la ligne précédente.
    result = []
    for line in lines:
        stripped = line.strip() 
        # Balise fermante seule sur la ligne (ex: </unordered_list>)
        if re.match(r"^</[\w]+>$", stripped) and result: # Explication "^</[\w]+>$" : ^ = début de ligne, </ = balise fermante, [\w]+ = nom de la balise (lettres, chiffres ou _), > = fin de la balise, $ = fin de ligne. Si la ligne correspond à ce pattern et qu'il y a au moins une ligne dans result, on fusionne.
            result[-1] = result[-1] + stripped
        else:
            result.append(line)
    return result

def reorder_page(lines: list[str]) -> list[str]:
    # Trie les lignes d'une page par y0 croissant.
    # Les lignes sans y0 sont conservées en tête dans leur ordre d'origine.
    with_y0 = [(extract_y0(l), l) for l in lines]
    no_y0 = [l for y0, l in with_y0 if y0 is None]
    sortable = [(y0, l) for y0, l in with_y0 if y0 is not None]
    sortable.sort(key = lambda x: x[0])
    return no_y0 + [l for _, l in sortable] # Les lignes sans y0 restent en tête, suivies des lignes triées par y0

def reorder_doctags(input_path: Path, output_path: Path) -> None:
    content = input_path.read_text(encoding = "utf-8") # Vériier le type d'encodage du fichier source UTF-8, Unicode ou autre

    # Supprime les balises <doctag> et </doctag> existantes pour retravailler proprement
    content = re.sub(r"</?doctag>", "", content).strip() # Explication "</?doctag>" : on cherche les balises <doctag> ou </doctag> et on les remplace par une chaîne vide.
    lines = content.splitlines()

    # Fusionne les balises fermantes seules avec la ligne précédente
    lines = merge_closing_tags(lines)

    # Découpe en pages (chaque page_footer ou page_break = fin de page)
    pages = []
    current_page = []

    for line in lines:
        current_page.append(line)
        if "<page_break>" in line or "<page_footer>" in line:
            pages.append(current_page)
            current_page = []

    if current_page:
        pages.append(current_page)

    # 4. Tri de chaque page indépendamment par y0
    result = []
    for page in pages:
        result.extend(reorder_page(page))

    # 5. Réenveloppe dans <doctag>...</doctag>
    final = "<doctag>" + "\n".join(result) + "\n</doctag>"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final, encoding="utf-8")
    print(f"Doctags réordonné : {output_path}")

if __name__ == "__main__":
    DOC_NAME = os.environ.get("DOC_NAME", "")
    project_root = Path(__file__).resolve().parent.parent  # → matthias_guideline_preprocessing/
    base = project_root / "data" / "output_files" / "stage1_test"
    src = base / DOC_NAME / f"{DOC_NAME}.doctags"
    dst = base / DOC_NAME / f"{DOC_NAME}_reordered.doctags"
    print(f"Looking for: {src}")
    print(f"Exists: {src.exists()}")
    reorder_doctags(src, dst)