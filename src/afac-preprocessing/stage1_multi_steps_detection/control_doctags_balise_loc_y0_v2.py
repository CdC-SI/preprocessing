from pathlib import Path
import re
import os
import sys
from dataclasses import dataclass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import load_vlm_config
config = load_vlm_config()


@dataclass
class Block:
    raw: str
    y0: int | None
    x0: int | None
    is_list_item: bool = False


def extract_xy0(s: str) -> tuple[int | None, int | None]:
    # <loc_x0><loc_y0>...
    m = re.search(r"<loc_(\d+)><loc_(\d+)>", s)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def parse_blocks(content: str) -> list[Block]:
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
    # no coords first (stable), then y0, then x0, then stable order
    indexed = list(enumerate(blocks))
    no_pos = [(i, b) for i, b in indexed if b.y0 is None]
    with_pos = [(i, b) for i, b in indexed if b.y0 is not None]

    with_pos.sort(key=lambda t: (t[1].y0, t[1].x0 if t[1].x0 is not None else 10**9, t[0]))
    return [b for _, b in no_pos] + [b for _, b in with_pos]


def render_blocks(blocks: list[Block]) -> str:
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