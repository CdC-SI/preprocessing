from pathlib import Path
import re
import os
import sys
from dataclasses import dataclass

# Appel des fonctions de configuration pour récupérer les chemins et paramètres nécessaires
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()

# Class Block pour stocker les informations d'un bloc de texte extrait des doctags
@dataclass
class Block:
    raw: str
    y0: int | None
    x0: int | None
    is_list_item: bool = False


def extract_xy0(s: str) -> tuple[int | None, int | None]:
    """
    Docstring pour extraire x0 et y0 d'une ligne de doctags
     - Utilise une expression régulière pour trouver les coordonnées x0 et y0 dans le format <loc_x0><loc_y0>.
     - Si les coordonnées ne sont pas trouvées, retourne (None, None).
     - Si elles sont trouvées, les convertit en entiers et les retourne sous forme de tuple (x0, y0).
    
    :param s: Description
    :type s: str
    :return: Description
    :rtype: tuple[int | None, int | None]
    """
    # <loc_x0><loc_y0>...
    match = re.search(r"<loc_(\d+)><loc_(\d+)>", s) # s pour string, \d+ pour un ou plusieurs chiffres, les parenthèses pour capturer les groupes
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def parse_blocks(content: str) -> list[Block]:
    """
    Docstring pour parse_blocks
    - Traite le contenu d'un fichier .doctags pour extraire les blocs de texte, en tenant compte des listes à puces.
    - Sépare le contenu en lignes, en supprimant les lignes vides.
    - Parcourt les lignes pour identifier les blocs de texte et les éléments de liste à puces, en utilisant les balises <unordered_list> et <list_item>.
    - Pour chaque bloc ou élément de liste, extrait les coordonnées x0 et y0 à l'aide de la fonction extract_xy0
    - Stocke les informations dans des instances de la classe Block.
    
    :param content: Description
    :type content: str
    :return: Description
    :rtype: list[Block]
    """
    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    blocks: list[Block] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # Expand unordered_list into list_item blocks
        if "<unordered_list>" in line:
            ul_parts = [line]
            i += 1
            while i < len(lines):
                ul_parts.append(lines[i])
                if "</unordered_list>" in lines[i]:
                    i += 1
                    break
                i += 1

            ul_text = "\n".join(ul_parts)
            items = re.findall(r"<list_item>.*?</list_item>", ul_text, flags=re.DOTALL)
            for it in items:
                x0, y0 = extract_xy0(it)
                blocks.append(Block(raw=it.replace("\n", "").strip(), y0=y0, x0=x0, is_list_item=True))
            continue

        # Regular one-line block
        x0, y0 = extract_xy0(line)
        blocks.append(Block(raw=line, y0=y0, x0=x0, is_list_item=False))
        i += 1

    return blocks


def split_pages(blocks: list[Block]) -> list[list[Block]]:
    """
    Docstring for split_pages
    - Sépare une liste de blocs en pages en fonction des balises de pagination présentes dans les blocs.
    - Parcourt la liste de blocs et regroupe les blocs jusqu'à ce qu'une balise de fin de page 
    - (<page_footer> ou <page_break>) soit rencontrée, indiquant la fin d'une page.
    - Chaque fois qu'une balise de fin de page est trouvée, le groupe de blocs accumulé est ajouté à la liste des pages
    - Un nouveau groupe est commencé pour la page suivante.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: list[list[Block]]
    """
    pages: list[list[Block]] = []
    cur: list[Block] = []

    for b in blocks:
        cur.append(b)
        if "<page_footer>" in b.raw or "<page_break>" in b.raw:
            pages.append(cur)
            cur = []

    if cur:
        pages.append(cur)

    return pages


def sort_page(blocks: list[Block]) -> list[Block]:
    """
    Docstring for sort_page
    - Trie les blocs d'une page en fonction de leurs coordonnées y0 (et x0 pour départager les blocs avec le même y0), 
    - en conservant l'ordre stable pour les blocs sans coordonnées.
    - Les blocs sans coordonnées (y0 = None) sont placés en premier, dans leur ordre d'origine.
    - Les blocs avec coordonnées sont triés par y0 croissant, puis par x0 croissant pour les blocs ayant le même y0.
    - En cas d'égalité sur y0 et x0, l'ordre d'origine est conservé.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: list[Block]
    """
    # no coords first (stable), then y0, then x0, then stable order
    indexed = list(enumerate(blocks))
    no_pos = [(i, b) for i, b in indexed if b.y0 is None]
    with_pos = [(i, b) for i, b in indexed if b.y0 is not None]

    with_pos.sort(key=lambda t: (t[1].y0, t[1].x0 if t[1].x0 is not None else 10**9, t[0]))
    return [b for _, b in no_pos] + [b for _, b in with_pos]


def render_blocks(blocks: list[Block]) -> str:
    """
    Docstring for render_blocks
    - Convertit une liste de blocs triés en une chaîne de caractères formatée, en gérant les balises de liste à puces.
    - Parcourt les blocs et construit une représentation textuelle en ajoutant les balises :
    <unordered_list> et </unordered_list> autour des éléments de liste à puces.
    - Maintient une variable d'état pour savoir si l'on est actuellement à l'intérieur d'une liste à puces
    - Les blocs qui ne sont pas des éléments de liste à puces sont ajoutés directement à la sortie
    - Tandis que les éléments de liste à puces sont enveloppés dans les balises appropriées.

    :param blocks: Description
    :type blocks: list[Block]
    :return: Description
    :rtype: str
    """
    out: list[str] = []
    in_ul = False

    for b in blocks:
        if b.is_list_item:
            if not in_ul:
                out.append("<unordered_list>")
                in_ul = True
            out.append(b.raw)
        else:
            if in_ul:
                out.append("</unordered_list>")
                in_ul = False
            out.append(b.raw)

    if in_ul:
        out.append("</unordered_list>")

    return "\n".join(out)


def reorder_doctags(input_path: Path, output_path: Path) -> None:
    """
    Docstring for reorder_doctags
    - Réordonne les blocs d'un fichier .doctags en fonction de leurs coordonnées y0 (et x0 pour départager les blocs avec le même y0),
    en conservant l'ordre stable pour les blocs sans coordonnées.
    - Lit le contenu du fichier .doctags, supprime les balises globales <doctag> et </doctag>, et traite le contenu pour extraire les blocs de texte.
    - Sépare les blocs en pages en fonction des balises de pagination, puis trie chaque page indépendamment en fonction de leurs coordonnées.
    - Enfin, réassemble les pages triées et les enveloppe à nouveau dans les balises <doctag>...</doctag> avant de les écrire dans le fichier de sortie.

    :param input_path: Description
    :type input_path: Path
    :param output_path: Description
    :type output_path: Path
    """
    content = input_path.read_text(encoding="utf-8")
    content = re.sub(r"</?doctag>\s*", "", content).strip()

    blocks = parse_blocks(content)
    pages = split_pages(blocks)

    result_pages = []
    for p in pages:
        sorted_p = sort_page(p)  # y0 then x0
        result_pages.append(render_blocks(sorted_p))

    final = "<doctag>\n" + "\n".join(result_pages) + "\n</doctag>\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final, encoding="utf-8")
    print(f"Doctags réordonné : {output_path}")


if __name__ == "__main__":
    DOC_NAME = os.environ.get("DOC_NAME", "")
    project_root = Path(__file__).resolve().parent.parent
    base = project_root / "data" / "output_files" / "stage1_test"
    src = base / DOC_NAME / f"{DOC_NAME}.doctags"
    dst = base / DOC_NAME / f"{DOC_NAME}_reordered.doctags"
    reorder_doctags(src, dst)