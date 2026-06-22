"""
markdown_utils.py — Post-traitements Markdown partagés.
"""


def apply_markdown_transforms(text: str) -> str:
    """
    Point d'entrée conservé pour compatibilité avec les appels existants dans le pipeline.
    Les transformations de balises personnalisées (couleur, soulignement) sont désormais
    gérées directement par le VLM en stage 10.

    :param text: texte Markdown
    :return: texte inchangé
    """
    return text
