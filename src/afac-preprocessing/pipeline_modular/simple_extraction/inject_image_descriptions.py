"""
Stage 4b - Injection des descriptions d'images dans le Markdown final.
Script : inject_image_descriptions.py

Remplace les marqueurs [[[IMAGE_DESC:N]]] laissés par description_image_context.py
avec les descriptions VLM issues du fichier _image_descriptions.md.

Les descriptions sont injectées APRÈS markdown_control_vlm.py (stage 10),
garantissant qu'elles ne peuvent pas être supprimées par les étapes VLM précédentes.

Usage :
    uv run python inject_image_descriptions.py --input data/input_files/MonDoc.pdf
    uv run python inject_image_descriptions.py --dotenv .env.test
    uv run python inject_image_descriptions.py \\
        --markdown data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \\
        --descriptions data/output_files_preprocessing/MonDoc/MonDoc_image_descriptions.md \\
        --output data/output_files_preprocessing/MonDoc/MonDoc_final.md
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from utils.paths import project_root, load_env, resolve_doc_name

_log = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\[\[\[IMAGE(?:\\)?_DESC:(\d+)\]\]\]")


def parse_image_descriptions_md(descriptions_path: Path) -> dict[int, str]:
    """
    Docstring for parse_image_descriptions_md
    Parse le fichier _image_descriptions.md et retourne {index: description}.
    Seules les entrées avec statut OK sont extraites.

    :param descriptions_path: Description
    :type descriptions_path: Path
    :return: Description
    :rtype: dict[int, str]
    
    """
    content = descriptions_path.read_text(encoding="utf-8")
    descriptions: dict[int, str] = {}

    for section in re.split(r"\n---\n", content):
        section = section.strip()
        m = re.match(r"## OK - Image (\d+)/\d+[^\n]*\n\n(.*)", section, re.DOTALL)
        if m:
            idx = int(m.group(1))
            desc = m.group(2).strip()
            if desc:
                descriptions[idx] = desc
                _log.debug("Description chargée pour IMAGE_DESC:%d (%d chars)", idx, len(desc))

    _log.info("%d description(s) chargée(s) depuis %s", len(descriptions), descriptions_path.name)
    return descriptions


def inject_descriptions(markdown_content: str, descriptions: dict[int, str]) -> tuple[str, int, int]:
    """
    Docstring for inject_descriptions
    Remplace les marqueurs [[[IMAGE_DESC:N]]] par les descriptions correspondantes.
    Les marqueurs dans un élément de liste sont inlinés (sans sauts de ligne).
    :return: (markdown mis à jour, nombre injectés, nombre manquants)

    :param markdown_content: Description
    :type markdown_content: str
    :param descriptions: Description
    :type descriptions: dict[int, str]
    :return: Description
    :rtype: tuple[str, int, int]
    """
    injected = 0
    missing = 0
    result: list[str] = []

    for line in markdown_content.splitlines(keepends=True):
        m = PLACEHOLDER_RE.search(line)
        if not m:
            result.append(line)
            continue

        idx = int(m.group(1))
        desc = descriptions.get(idx)

        if not desc:
            _log.warning("Aucune description pour IMAGE_DESC:%d — marqueur conservé", idx)
            result.append(line)
            missing += 1
            continue

        stripped = line.lstrip()
        is_list_item = bool(re.match(r"[-*+] |\d+\. ", stripped))

        if is_list_item:
            desc_text = desc.replace("\n", " ").strip()
        else:
            desc_text = desc

        new_line = PLACEHOLDER_RE.sub(desc_text, line)
        if not new_line.endswith("\n"):
            new_line += "\n"

        result.append(new_line)
        injected += 1
        _log.info("IMAGE_DESC:%d injecté (%d chars)", idx, len(desc))

    return "".join(result), injected, missing


def run(markdown_path: Path, descriptions_path: Path, output_path: Path) -> None:
    """
    Docstring for run
    Injecte les descriptions dans le Markdown et sauvegarde le résultat.
    
    :param markdown_path: Description
    :type markdown_path: Path
    :param descriptions_path: Description
    :type descriptions_path: Path
    :param output_path: Description
    :type output_path: Path
    """
    if not markdown_path.exists():
        raise FileNotFoundError(f"Markdown introuvable : {markdown_path}")
    if not descriptions_path.exists():
        raise FileNotFoundError(f"Descriptions introuvables : {descriptions_path}")

    _log.info("Markdown source  : %s", markdown_path)
    _log.info("Descriptions     : %s", descriptions_path)
    _log.info("Sortie           : %s", output_path)

    descriptions = parse_image_descriptions_md(descriptions_path)
    content = markdown_path.read_text(encoding="utf-8")

    found = PLACEHOLDER_RE.findall(content)
    no_placeholders = not found
    no_descriptions = not descriptions

    if no_placeholders and no_descriptions:
        # Normal case: image description was disabled at step 06.
        _log.info("Aucune description ni marqueur — descriptions désactivées. Fichier copié tel quel.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    if no_placeholders:
        _log.warning(
            "Descriptions disponibles mais aucun marqueur [[[IMAGE_DESC:N]]] trouvé dans %s. "
            "Vérifier que description_image_context.py utilise bien les placeholders.",
            markdown_path.name,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    _log.info("%d marqueur(s) trouvé(s) : %s", len(found), [int(i) for i in found])

    if no_descriptions:
        _log.warning("Marqueurs présents mais aucune description disponible — fichier copié sans injection.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        return

    updated, injected, missing = inject_descriptions(content, descriptions)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(updated, encoding="utf-8")
    _log.info("Injection terminée : %d injecté(s), %d manquant(s)", injected, missing)
    _log.info("Markdown final sauvegardé : %s", output_path)

    if missing:
        _log.warning(
            "%d description(s) manquante(s). "
            "Vérifier _image_descriptions.md ou relancer description_image_context.py.",
            missing,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Injecte les descriptions VLM dans le Markdown final en remplaçant "
            "les marqueurs [[[IMAGE_DESC:N]]]. "
            "À exécuter après markdown_control_vlm.py (stage 10)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  uv run python inject_image_descriptions.py --input data/input_files/MonDoc.pdf\n"
            "  uv run python inject_image_descriptions.py --dotenv .env.test\n"
            "  uv run python inject_image_descriptions.py \\\n"
            "      --markdown  data/output_files_preprocessing/MonDoc/MonDoc_vlm_check.md \\\n"
            "      --descriptions data/output_files_preprocessing/MonDoc/MonDoc_image_descriptions.md \\\n"
            "      --output data/output_files_preprocessing/MonDoc/MonDoc_final.md\n"
        ),
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=None,
        help="Chemin vers le PDF source (pour résoudre le stem du document).",
    )
    parser.add_argument(
        "--markdown", "-m",
        type=Path,
        default=None,
        help="Markdown à traiter. Défaut : data/output_files_preprocessing/<stem>/<stem>_vlm_check.md",
    )
    parser.add_argument(
        "--descriptions", "-d",
        type=Path,
        default=None,
        help="Fichier _image_descriptions.md. Défaut : data/output_files_preprocessing/<stem>/<stem>_image_descriptions.md",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Markdown de sortie. Défaut : data/output_files_preprocessing/<stem>/<stem>_final.md",
    )
    parser.add_argument(
        "--dotenv",
        type=Path,
        default=None,
        metavar="FICHIER",
        help="Fichier .env à charger pour résoudre DOC_NAME.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Niveau de journalisation. Défaut : INFO.",
    )
    return parser.parse_args()


def _resolve_stem(args: argparse.Namespace) -> str:
    if args.input:
        return args.input.resolve().stem.strip()
    return resolve_doc_name(args, primary_flag="--input")


def _resolve_markdown(args: argparse.Namespace, stem: str) -> Path:
    if args.markdown:
        return args.markdown.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_vlm_check.md"


def _resolve_descriptions(args: argparse.Namespace, stem: str) -> Path:
    if args.descriptions:
        return args.descriptions.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_image_descriptions.md"


def _resolve_output(args: argparse.Namespace, stem: str) -> Path:
    if args.output:
        return args.output.resolve()
    return project_root() / "data" / "output_files_preprocessing" / stem / f"{stem}_final.md"


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.dotenv:
        load_env(args.dotenv)

    stem = _resolve_stem(args)
    markdown_path = _resolve_markdown(args, stem)
    descriptions_path = _resolve_descriptions(args, stem)
    output_path = _resolve_output(args, stem)

    try:
        run(markdown_path, descriptions_path, output_path)
    except FileNotFoundError as e:
        _log.exception("%s", e)
        sys.exit(1)
    except Exception:
        _log.exception("Erreur inattendue lors de l'injection.")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
