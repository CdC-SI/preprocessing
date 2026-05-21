import re
from pathlib import Path

def parse_doctags_elements(lines):
    elements = []
    for line in lines:
        line_clean = re.sub(r"</?doctag>", "", line).strip()
        locs = re.findall(r"<loc_(\d+)>", line_clean)
        y0 = int(locs[1]) if len(locs) >= 2 else None
        x0 = int(locs[0]) if len(locs) >= 1 else None
        elements.append({
            "y0": y0,
            "x0": x0,
            "raw": line,
        })
    return elements

def regroup_by_page(elements):
    # Regroupe les éléments par page (détecte <page_header> ou <page_footer> pour changer de page)
    pages = []
    current_page = []
    for el in elements:
        if "<page_header>" in el["raw"] and current_page:
            pages.append(current_page)
            current_page = [el]
        else:
            current_page.append(el)
        if "<page_footer>" in el["raw"]:
            pages.append(current_page)
            current_page = []
    if current_page:
        pages.append(current_page)
    return pages

def sort_page_elements(page):
    # Trie les éléments avec y0, les autres restent à leur place d'origine
    with_y0 = [el for el in page if el["y0"] is not None]
    with_y0_sorted = sorted(with_y0, key=lambda el: (el["y0"], el["x0"] if el["x0"] is not None else 0))
    result = []
    idx = 0
    for el in page:
        if el["y0"] is not None:
            result.append(with_y0_sorted[idx])
            idx += 1
        else:
            result.append(el)
    return result

def reorder_doctags_by_y0(doctags_path: Path, output_path: Path):
    lines = doctags_path.read_text(encoding="utf-8").splitlines()
    elements = parse_doctags_elements(lines)
    pages = regroup_by_page(elements)
    pages_sorted = [sort_page_elements(page) for page in pages]
    new_lines = [el["raw"] for page in pages_sorted for el in page]
    output_path.write_text("\n".join(new_lines), encoding="utf-8")
    print(f"Doctags réorganisé sauvegardé : {output_path}")

if __name__ == "__main__":
    DOC_NAME = "Adhésion traitement" # CHANGER SELON LES TESTS
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    doctags_path = PROJECT_ROOT / "matthias_guideline_preprocessing/data/output_files/stage1_test" / DOC_NAME / f"{DOC_NAME}.doctags"
    output_dir = Path(f"preprocessing/matthias_guideline_preprocessing/data/output_files/stage1_test/{DOC_NAME}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{DOC_NAME}_reordered.doctags"
    reorder_doctags_by_y0(doctags_path, output_path)