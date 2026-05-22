from pathlib import Path
import re
from dotenv import load_dotenv
import os

# Chargement de .env.test
dotenv_path = Path(__file__).resolve().parent.parent / ".env.test"
print("Loading dotenv from:", dotenv_path.resolve(), "exists:", dotenv_path.exists())
load_dotenv(dotenv_path=dotenv_path)
load_dotenv()


def extract_y0(line: str) -> int | None:
    # Extrait le y0 (2ème <loc_N>) d'une ligne. Retourne None si absent.
    m = re.search(r"<loc_\d+><loc_(\d+)>", line)
    return int(m.group(1)) if m else None


def merge_closing_tags(lines: list[str]) -> list[str]:
    # Fusionne les balises fermantes seules sur une ligne avec la ligne précédente.
    # Ex: </unordered_list> seul → collé à la fin de la ligne précédente.
    result = []
    for line in lines:
        stripped = line.strip()
        # Balise fermante seule sur la ligne (ex: </unordered_list>)
        if re.match(r"^</[\w_]+>$", stripped) and result:
            result[-1] = result[-1] + stripped
        else:
            result.append(line)
    return result


def reorder_page(lines: list[str]) -> list[str]:

    # Trie les lignes d'une page par y0 croissant.
    # Les lignes sans y0 sont conservées en tête dans leur ordre d'origine.
    with_y0  = [(extract_y0(l), l) for l in lines]
    no_y0    = [l for y0, l in with_y0 if y0 is None]
    sortable = [(y0, l) for y0, l in with_y0 if y0 is not None]
    sortable.sort(key=lambda x: x[0])
    return no_y0 + [l for _, l in sortable]


def reorder_doctags(input_path: Path, output_path: Path) -> None:
    content = input_path.read_text(encoding="utf-8")

    # 1. Supprime les balises <doctag> et </doctag> existantes pour retravailler proprement
    content = re.sub(r"</?doctag>", "", content).strip()

    lines = content.splitlines()

    # 2. Fusionne les balises fermantes seules avec la ligne précédente
    lines = merge_closing_tags(lines)

    # 3. Découpe en pages (chaque page_footer ou page_break = fin de page)
    pages        = []
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
    base = Path("preprocessing/matthias_guideline_preprocessing/data/output_files/stage1_test")
    src = base / DOC_NAME / f"{DOC_NAME}.doctags"
    dst = base / DOC_NAME / f"{DOC_NAME}_reordered.doctags"
    reorder_doctags(src, dst)