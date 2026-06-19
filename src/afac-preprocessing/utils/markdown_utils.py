"""
markdown_utils.py — Post-traitements Markdown partagés.

Centralise les transformations de balises personnalisées (couleur, soulignement)
pour éviter la duplication entre les scripts du pipeline.
"""
import re

_COLOR_RE = re.compile(r'\[\[COLOR:([^\]]+)\]\](.*?)\[\[/COLOR\]\]', re.DOTALL)
_UNDERLINE_RE = re.compile(r'\\_\\_(.*?)\\_\\_', re.DOTALL)


def apply_markdown_transforms(text: str) -> str:
    """
    Applique les post-traitements des balises personnalisées couleur et soulignement.

    - [[COLOR:color]]texte[[/COLOR]] → <span style="color:color">texte</span>
    - \\_\\_texte\\_\\_ → <u>texte</u>

    :param text: texte Markdown à transformer
    :return: texte Markdown transformé
    """
    text = _COLOR_RE.sub(lambda m: f'<span style="color:{m.group(1)}">{m.group(2)}</span>', text)
    text = _UNDERLINE_RE.sub(lambda m: f'<u>{m.group(1)}</u>', text)
    return text
