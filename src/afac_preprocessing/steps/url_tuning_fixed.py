"""Deterministic (VLM-free) variant of url-tuning (08).

Opt-in through ``afac-preprocess run --deterministic-urls``: without the flag
the canonical VLM-based step runs unchanged. See ``core/registry.build_default_steps``.

Why this exists
----------------
The canonical ``url_tuning.py`` renders each page as an image and asks a VLM
to visually locate where each hyperlink belongs in the doctags. But
url-extraction (step 07, no VLM at all) has already computed, for every
link, its page, its exact anchor text and its URI — via PyMuPDF spatial
matching (``page.get_links()`` + ``page.get_text("words")``, see
``get_link_text()`` in ``url_extraction.py``). That reliable information is
thrown away by url-tuning, which re-derives it through the VLM — producing,
on at least one document ("TN - Fortune et revenu acquis sous forme de
rente", see docs/rapport_erreur_resolution.md), broken tags and lost links.

``DeterministicLinkInjector`` re-injects the links from
``hyperlinks_data_<doc>.jsonl`` directly into the doctags by anchor-text
search — the text is already known, it only needs to be found in Docling's
doctags and wrapped in Markdown syntax — with no VLM call and no image
render. Verified on the corpus: recovers URLs the VLM path drops or corrupts,
with zero regression on documents it already handled correctly.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from ..core.step import StepResult, StepStatus
from .url_tuning import (
    UrlTuningStep,
    assemble_doctags,
    get_links_for_page,
    load_jsonl_links,
    split_doctags_by_page,
)

if TYPE_CHECKING:
    from ..context import PipelineContext

_log = logging.getLogger(__name__)


@dataclass
class PageCanvas:
    """A page's doctags being injected into, with memory of already-replaced
    zones.

    This memory is what stops one link from overwriting another, and it
    lives on the page rather than on a single method call: once a URL is
    injected, it becomes part of the searched text, and a short anchor
    ("art") could otherwise match INSIDE an already-placed URL
    ("...fr#art_17"), producing an unreadable nested link. Keeping the state
    at the page level guarantees the protection across ALL injection passes,
    including the cross-page fallback of
    DeterministicLinkInjector.inject_into_doctags().
    """

    text: str
    injected: list[tuple[int, int]] = field(default_factory=list)

    def is_free(self, start: int, end: int) -> bool:
        """True if the (start, end) span overlaps no already-injected zone."""
        return not any(start < done_end and done_start < end for done_start, done_end in self.injected)

    def substitute(self, start: int, end: int, replacement: str) -> None:
        """Replaces the span with *replacement*, then records the occupied
        zone and shifts zones located after the insertion point."""
        self.text = self.text[:start] + replacement + self.text[end:]
        shift = len(replacement) - (end - start)
        self.injected = [
            (s, e) if e <= start else (s + shift, e + shift) for s, e in self.injected
        ]
        self.injected.append((start, start + len(replacement)))


@dataclass
class DeterministicLinkInjector:
    """Pure link-injection logic on a doctags, no I/O and no VLM.

    Reusable as-is by other scripts: no dependency on a file path, only
    strings and link dicts in/out.
    """

    case_insensitive: bool = True

    #: Number of matching tiers, cf. _patterns_for().
    MATCH_TIERS: ClassVar[int] = 3

    #: Characters PyMuPDF and Docling don't transcribe identically. Each
    #: string is an equivalence class: any of its characters may stand for
    #: any other. Observed on the corpus: Docling normalizes the em-dash into
    #: a hyphen (the "bpanda" links — the largest failure group — hinged on
    #: exactly this) and the curly apostrophe into a straight one.
    _EQUIVALENT_CHARS: ClassVar[tuple[str, ...]] = (
        "'’‘`´",
        "-–—‒―‐‑",
        '"“”«»',
    )

    #: Punctuation Docling can drop at an anchor's edge (observed: the anchor
    #: "(RAVS" against a doctags that reads "RAVS, art. 34d").
    _BORDER_PUNCTUATION: ClassVar[str] = "().,;:!?[]\"'’‘`´«»-–—"

    def _char_pattern(self, char: str) -> str:
        """Regex fragment for one character, widened to its equivalence class."""
        for group in self._EQUIVALENT_CHARS:
            if char in group:
                return f"[{re.escape(group)}]"
        return re.escape(char)

    def _fuzzy_pattern(self, anchor_text: str, *, optional_borders: bool) -> re.Pattern[str] | None:
        """Builds a pattern tolerant to transcription differences between
        PyMuPDF (source text) and Docling (reconstructed text), or None if
        the anchor has no significant character.

        Two tolerances always active, both observed on the corpus:

        - **spacing**: the anchor's own spaces are ignored and an optional
          ``\\s*`` is inserted between every pair of significant characters,
          since Docling sometimes moves a space around punctuation
          ("(art." becomes "( art.") ;
        - **equivalence classes**: cf. _EQUIVALENT_CHARS.

        *optional_borders* adds a third tolerance, reserved for the last
        tier of find_anchor_span(): leading/trailing punctuation becomes
        optional, since Docling sometimes drops a parenthesis. Never enabled
        upfront: it shortens the anchor, making it less discriminating, and
        a short anchor like "(art." would end up matching a bare "art"
        elsewhere on the page.
        """
        chars = [char for char in anchor_text if not char.isspace()]
        if not chars:
            return None

        tokens = [self._char_pattern(char) for char in chars]
        if optional_borders:
            for index in self._border_punctuation_indices(chars):
                tokens[index] += "?"

        flags = re.IGNORECASE if self.case_insensitive else 0
        return re.compile(r"\s*".join(tokens), flags)

    def _border_punctuation_indices(self, chars: list[str]) -> set[int]:
        """Indices of leading/trailing punctuation in the anchor.

        Empty if the anchor is entirely punctuation — making it optional
        would produce a pattern matching the empty string anywhere.
        """
        if all(char in self._BORDER_PUNCTUATION for char in chars):
            return set()

        indices: set[int] = set()
        for index in range(len(chars)):
            if chars[index] not in self._BORDER_PUNCTUATION:
                break
            indices.add(index)
        for index in reversed(range(len(chars))):
            if chars[index] not in self._BORDER_PUNCTUATION:
                break
            indices.add(index)
        return indices

    @staticmethod
    def _longest_anchor_first(links: list[dict]) -> list[dict]:
        """Longest anchors first: a long anchor is more discriminating, and
        handling it first stops a shorter anchor contained within it from
        taking its place."""
        return sorted(links, key=lambda link: -len(link.get("text") or ""))

    def _patterns_for(self, anchor_text: str) -> list[re.Pattern[str] | None]:
        """The matching tiers, from strictest to most permissive:

        0. exact literal match ;
        1. spacing tolerance + character equivalence classes ;
        2. same, plus border punctuation made optional.

        An entry is None when the tier doesn't apply to this anchor. The
        list length is stable (MATCH_TIERS) so a tier's index keeps the same
        meaning across anchors.
        """
        return [
            re.compile(re.escape(anchor_text)),
            self._fuzzy_pattern(anchor_text, optional_borders=False),
            self._fuzzy_pattern(anchor_text, optional_borders=True),
        ]

    def find_anchor_span(
        self, canvas: PageCanvas, anchor_text: str, *, tier: int | None = None
    ) -> tuple[int, int] | None:
        """Locates anchor_text on the page, ignoring any occurrence that
        overlaps an already-injected zone — otherwise two distinct PDF links
        sharing the same anchor text (e.g. the same legal reference cited
        twice) would both re-inject into the first occurrence.

        The search proceeds tier by tier, strictest to most permissive (cf.
        _patterns_for), and stops at the first one giving a free occurrence.

        :param canvas: page currently being injected into.
        :param anchor_text: anchor text to locate (already extracted by
            url-extraction).
        :param tier: only try this tier. None (default) tries them all in
            order — what a call scoped to a single page wants.
            inject_into_doctags() passes an explicit tier so every page is
            compared at equal strictness during its fallback.
        :return: span (start, end) of the first free occurrence, or None.
        """
        if not anchor_text:
            return None

        patterns = self._patterns_for(anchor_text)
        selected = patterns if tier is None else patterns[tier : tier + 1]

        for pattern in selected:
            if pattern is None:
                continue
            for match in pattern.finditer(canvas.text):
                if canvas.is_free(*match.span()):
                    return match.span()

        return None

    def inject_link(self, canvas: PageCanvas, link: dict, *, tier: int | None = None) -> bool:
        """Injects one link into the page if its anchor text can be found there.

        The anchor is wrapped in Markdown syntax [text](url), never an
        <a href> tag: <a href> isn't a doctags tag Docling's parser
        recognizes (verified empirically — the link disappears outright at
        markdown export), whereas inline Markdown syntax passes through the
        parser as plain text and survives, at the cost of underscore
        escaping repaired downstream by
        markdown_utils.repair_escaped_link_destinations().

        Enclosing tags (<list_item>, <text>, ...) are never touched, unlike
        the VLM which sometimes rewrites them incorrectly.

        :param tier: matching tier to use, cf. find_anchor_span().
        :return: True if the link was injected, False if it remains unmatched.
        """
        uri = link.get("hyperlink")
        anchor_text = (link.get("text") or "").strip()
        if not uri or not anchor_text or anchor_text == "No text":
            return False

        span = self.find_anchor_span(canvas, anchor_text, tier=tier)
        if span is None:
            return False

        start, end = span
        canvas.substitute(start, end, f"[{canvas.text[start:end]}]({uri})")
        return True

    def inject_into_page(self, page_tags: str, page_links: list[dict]) -> tuple[str, list[dict]]:
        """Injects a list of links into a single page's doctags.

        Convenience wrapper around PageCanvas for an isolated call;
        inject_into_doctags() drives the canvases directly so their
        injection memory survives from one pass to the next.

        :param page_tags: doctags content of one page.
        :param page_links: links (dicts from hyperlinks_data_*.jsonl)
            belonging to this page.
        :return: (modified doctags, list of unmatched links)
        """
        canvas = PageCanvas(page_tags)
        unmatched = [
            link for link in self._longest_anchor_first(page_links) if not self.inject_link(canvas, link)
        ]
        return canvas.text, unmatched

    def inject_into_doctags(self, doctags: str, links: list[dict], n_pages: int) -> tuple[str, list[dict]]:
        """Splits the doctags by page (same logic as url-tuning), injects
        each page's links, then reassembles.

        Cross-page fallback: a link not found on the page the PDF indicated
        (link["page_number"]) is retried on the other pages before being
        given up on. Needed because Docling's page split isn't always
        faithful to the PDF's real split on this corpus (two pages' content
        merged into a single <page_footer>/<page_break>, cf.
        docs/rapport_erreur_resolution.md) — the "PDF page -> doctags page"
        map isn't 100% reliable, only the anchor text is.

        Both passes share the same PageCanvas objects: a fallback link can
        therefore never overwrite a link placed during the first pass.

        In the fallback, matching tiers take priority over page order: every
        page is tried at exact match first, then all of them tolerant, etc.
        Walking page by page while trying every tier would let a permissive
        match on an early page win over the exact match available further
        along (observed: the anchor "(art." captured inside "Dep[art]ement"
        in the page-1 header, while its exact "(art." sat on page 3).

        :param doctags: full doctags of the document.
        :param links: all of the document's links (hyperlinks_data_*.jsonl).
        :param n_pages: real page count (PDF).
        :return: (reassembled full doctags, links still unmatched after fallback)
        """
        canvases = {
            page_num: PageCanvas(page_tags)
            for page_num, page_tags in split_doctags_by_page(doctags, n_pages).items()
        }

        unmatched: list[dict] = []
        for page_num, canvas in canvases.items():
            page_links = self._longest_anchor_first(get_links_for_page(links, page_num))
            unmatched.extend(link for link in page_links if not self.inject_link(canvas, link))

        final_unmatched = [
            link
            for link in self._longest_anchor_first(unmatched)
            # any() short-circuits: the link is placed on the first
            # (tier, page) combination that matches, nothing else is tried —
            # tier being the outer loop, strictness takes priority.
            if not any(
                self.inject_link(canvas, link, tier=tier)
                for tier in range(self.MATCH_TIERS)
                for canvas in canvases.values()
            )
        ]

        assembled = assemble_doctags({num: canvas.text for num, canvas in canvases.items()})
        return assembled, final_unmatched


class DeterministicUrlTuningStep(UrlTuningStep):
    """Opt-in variant of ``url-tuning``: injects hyperlinks already extracted
    by url-extraction via anchor-text search, no VLM call and no image
    render. Inherits ``name``, ``outputs()`` and the ``_source_doctags()``
    resolution from ``UrlTuningStep``; ``requires_vlm`` is overridden to
    False so a run with this variant active never needs VLM connectivity for
    this step, and ``execute()`` replaces the VLM call with
    ``DeterministicLinkInjector``.
    """

    requires_vlm = False

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [self._source_doctags(ctx), ctx.workspace.hyperlinks_jsonl]

    def execute(self, ctx: PipelineContext) -> StepResult:
        ws = ctx.workspace
        doctags_path = self._source_doctags(ctx)
        jsonl_path = ws.hyperlinks_jsonl
        output_path = ws.url_vlm_doctags

        doctags = doctags_path.read_text(encoding="utf-8")
        links = load_jsonl_links(jsonl_path)

        if not links:
            _log.info("No links for this document — doctags copied unchanged.")
            output_path.write_text(doctags, encoding="utf-8")
            return StepResult(StepStatus.OK, outputs=self.outputs(ctx), message="no links")

        n_pages = self._pdf_page_count(ctx)
        injector = DeterministicLinkInjector()
        new_doctags, unmatched = injector.inject_into_doctags(doctags, links, n_pages)
        output_path.write_text(new_doctags, encoding="utf-8")

        if unmatched:
            for link in unmatched:
                _log.warning("Anchor not found: %r -> %s", link.get("text"), link.get("hyperlink"))

        _log.info(
            "%d link(s) total, %d unmatched, deterministic injection (no VLM).",
            len(links), len(unmatched),
        )
        return StepResult(
            StepStatus.OK,
            outputs=self.outputs(ctx),
            message=f"{len(links) - len(unmatched)}/{len(links)} injected",
        )

    @staticmethod
    def _pdf_page_count(ctx: PipelineContext) -> int:
        from ..utils.pdf_utils import pdf_page_count

        return pdf_page_count(ctx.workspace.source_pdf) or 1
