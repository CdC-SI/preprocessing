"""
Stage 4 - Script de conversion des doctags enrichis en Markdown
Script 1 : convert_doctags_to_markdown.py

Ce script convertit les doctags enrichis (avec les URL extraites et intégrées) en Markdown,
en utilisant la bibliothèque Docling pour interpréter les doctags et générer le Markdown final.
"""
import logging
import os
import re
from pathlib import Path
from docling_core.types.doc.document import DocTagsDocument, DoclingDocument

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_log = logging.getLogger(__name__)


def _split_pages(content: str) -> str:
    """
    Si le contenu est un seul bloc <doctag>, le découpe en un bloc par page en utilisant
    </page_footer> comme délimiteur, ce que from_multipage_doctags_and_images attend.
    Sans ce découpage, Docling s'arrête après la première page et ignore le reste.

    :param content: contenu doctags brut
    :type content: str
    :return: contenu découpé en blocs <doctag> par page
    :rtype: str
    """
    if content.count("<doctag>") > 1:
        return content  # déjà au bon format multi-pages

    inner = re.sub(r"^\s*</?doctag>\s*", "", content.strip(), flags=re.DOTALL)
    inner = re.sub(r"\s*</doctag>\s*$", "", inner, flags=re.DOTALL)
    parts = re.split(r"(?<=</page_footer>)", inner)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return content  # document d'une seule page, rien à faire

    return "\n".join(f"<doctag>\n{p}\n</doctag>" for p in parts)


def _hoist_misplaced_tags(content: str) -> str:
    """
    Docling ne peut pas gérer les <section_header_level_N> ou <unordered_list> imbriqués
    dans un <ordered_list>. Il les écrase dans la liste, perdant les en-têtes et les frontières de section.
    Extrait ces balises des blocs <ordered_list> et les place juste après le </ordered_list> correspondant.

    :param content: contenu doctags
    :type content: str
    :return: contenu avec les balises mal placées extraites
    :rtype: str
    """
    HOIST = re.compile(
        r"(<section_header_level_\d[^>]*>.*?</section_header_level_\d>|"
        r"<unordered_list>.*?</unordered_list>)",
        re.DOTALL,
    )
    OL = re.compile(r"<ordered_list>(.*?)</ordered_list>", re.DOTALL)

    def _fix_ol(m: re.Match) -> str:
        inner = m.group(1)
        hoisted: list[str] = []

        def _extract(tag_m: re.Match) -> str:
            hoisted.append(tag_m.group(0))
            return ""

        cleaned = HOIST.sub(_extract, inner)
        result = f"<ordered_list>{cleaned}</ordered_list>"
        if hoisted:
            result += "\n" + "\n".join(hoisted)
        return result

    return OL.sub(_fix_ol, content)


def _replace_color(match: re.Match) -> str:
    """
    Remplace les balises personnalisées [[COLOR:color]]texte[[/COLOR]]
    par des spans HTML <span style="color:color">texte</span>.

    :param match: résultat de re.sub avec groupes (color, texte)
    :return: span HTML avec la couleur appliquée
    :rtype: str
    """
    color = match.group(1)
    text = match.group(2)
    return f'<span style="color:{color}">{text}</span>'


def _replace_underline(match: re.Match) -> str:
    """
    Remplace les balises de soulignement personnalisées \\_\\_texte\\_\\_
    par des balises HTML <u>texte</u>.

    :param match: résultat de re.sub avec groupe (texte)
    :return: balise HTML <u> avec le texte souligné
    :rtype: str
    """
    return f'<u>{match.group(1)}</u>'


def main() -> None:
    GEN_ID = os.environ.get("GEN_ID", "")
    DOC_NAME = os.environ.get("DOC_NAME", "")

    doctags_path = (
        PROJECT_ROOT / "data" / "output_files" / "stage3_test"
        / DOC_NAME / f"{DOC_NAME}_reordered_with_tables_pictures_url_vlm.doctags"
    )
    output_dir = PROJECT_ROOT / "data" / "output_files" / "stage4_test"
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{DOC_NAME}{GEN_ID}.md"

    _log.info("Looking for doctags: %s (exists=%s)", doctags_path, doctags_path.exists())

    content = doctags_path.read_text(encoding="utf-8")
    content = _split_pages(content)
    content = _hoist_misplaced_tags(content)

    doctags = DocTagsDocument.from_multipage_doctags_and_images(content, None)  # Conversion via Docling
    doc = DoclingDocument.load_from_doctags(doctags)
    markdown = doc.export_to_markdown()

    markdown = re.sub(
        r'\[\[COLOR:([^\]]+)\]\](.*?)\[\[/COLOR\]\]',  # balises couleur personnalisées → spans HTML
        _replace_color,
        markdown,
        flags=re.DOTALL,
    )
    markdown = re.sub(
        r'\\_\\_(.*?)\\_\\_',  # balises soulignement personnalisées → <u>
        _replace_underline,
        markdown,
        flags=re.DOTALL,
    )

    md_path.write_text(markdown, encoding="utf-8")
    _log.info("Markdown généré : %s", md_path)
    print(f"Markdown généré : {md_path}")


if __name__ == "__main__":
    main()
