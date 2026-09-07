"""
markdown_utils.py, Shared Markdown post-processing.
"""

from __future__ import annotations

import html
import re

_MD_LINK_DESTINATION = re.compile(r"\]\(([^)]*)\)")
_MD_ESCAPED_CHAR = re.compile(r"\\([_*\[\]()])")


def repair_escaped_link_destinations(markdown: str) -> str:
    """Repairs Markdown link destinations corrupted by the Docling export,
    which doesn't recognize inline ``[text](url)`` links written directly as
    plain text (rather than a native ``<a href>`` tag, which Docling's
    doctags parser doesn't understand at all — verified: such a link
    disappears outright at markdown export) and applies two destructive
    treatments to it:

    - Markdown special-character escaping (``_`` above all), turning
      ``#art_17`` into ``#art\\_17`` ;
    - HTML entity encoding, turning the ``&`` query-parameter separator into
      ``&amp;`` (observed on 70 occurrences across the AFAC corpus, on
      "bpanda" process URLs carrying several parameters).

    Both break the URL for any downstream consumer. Scoped to the inside of
    ``](...)`` only — an escaped underscore or HTML entity in the body text
    elsewhere is left untouched.

    Traced originally to url-tuning's deterministic variant
    (``steps/url_tuning_fixed.py``), which injects links as literal
    ``[text](url)`` text for exactly this reason, but applied unconditionally
    here since the VLM-based url-tuning path produces the same literal
    syntax often enough to need the same repair (observed on "TN - Fortune
    et revenu acquis sous forme de rente").

    :param markdown: Markdown text to repair.
    :return: The same text, with every link destination un-escaped.
    """

    def _unescape(match: re.Match[str]) -> str:
        destination = _MD_ESCAPED_CHAR.sub(r"\1", match.group(1))
        return f"]({html.unescape(destination)})"

    return _MD_LINK_DESTINATION.sub(_unescape, markdown)


def apply_markdown_transforms(text: str) -> str:
    """
    Entry point kept for compatibility with existing calls in the pipeline.
    Custom tag transformations (color, underline) are now handled
    directly by the VLM in stage 10.

    :param text: Markdown text
    :return: text with link destinations repaired (cf.
        repair_escaped_link_destinations); otherwise unchanged.
    """
    return repair_escaped_link_destinations(text)
