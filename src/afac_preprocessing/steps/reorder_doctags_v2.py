"""reorder-doctags v2 — rebuilds the reordered doctags from Docling's own JSON
instead of reconstructing page boundaries by parsing ``<page_footer>`` /
``<page_break>`` markers out of the flat ``.doctags`` text.

Not wired into the default pipeline — see ``core/registry.build_default_steps``
for how ``--fixed-tables`` opts into a sibling variant; the same mechanism
would apply here once this is validated.

Why this exists
----------------
``reorder_doctags.py`` (v1) works purely on the flat ``.doctags`` text: it
infers which page each block belongs to by counting ``<page_footer>`` and
``<page_break>`` tags, then re-sorts each inferred page's blocks by (y0, x0).
Two concrete failures were traced on "TN - Fortune et revenu acquis sous forme
de rente" (see ``docs/rapport_erreur_resolution.md``):

1. When the footer/break signals disagree with the true page count, the
   arbitration in ``split_pages()`` can produce an EMPTY inferred page that
   silently absorbs another page's content — verified: a checkbox genuinely on
   PDF page 2 ended up sorted, by y0, into a merged bucket with a paragraph
   from PDF page 3, landing *after* it in reading order (reversed).
2. The ``.doctags`` format itself cannot represent an element that spans two
   pages: it flattens a two-``prov`` text item into one ``<text>`` tag with 8
   raw ``loc_`` numbers, no page marker between them.

Docling's own JSON keeps neither ambiguity: each text item's ``prov`` list
carries an authoritative ``page_no`` (and a real PDF-point bbox, not the
normalized ~500 doctags scale). Rather than re-deriving page grouping from a
lossy text serialization, this variant loads the ``DoclingDocument`` from JSON
and calls its own ``export_to_doctags(pages={n})`` per page — the same
serializer Docling uses everywhere else, just page-filtered, so no bespoke
sorting/splitting logic is reimplemented here at all.

Verified on "TN - Fortune...", both failures above are gone: the checkbox
(page 2) and the paragraph (page 3) come out in the correct order, and the
page-spanning paragraph is emitted whole, attached to its first page, with no
loss or duplication.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from docling_core.types.doc.document import DoclingDocument

from ..exceptions import StepFailed
from .reorder_doctags import ReorderDoctagsStep

if TYPE_CHECKING:
    from ..context import PipelineContext
    from ..core.step import StepResult

_log = logging.getLogger(__name__)

#: Docling wraps each page-filtered export in its own <doctag>...</doctag>,
#: stripped before re-joining since the final result gets exactly one wrapper.
_DOCTAG_WRAPPER_RE = re.compile(r"^\s*<doctag>|</doctag>\s*$")

#: Every <page_break> in a single page's export is removed, not just the ones
#: at the edges (a first version only stripped edge ones). Docling's own
#: per-page export can embed one INSIDE a page's content, not only at its
#: boundary — confirmed on 3 documents in this corpus (e.g. "TE -
#: Agriculteurs" p.3, mid-table). Since page boundaries here come entirely
#: from prov[].page_no, none of these markers carry meaning inside a single
#: page's body. Left in place, sort_page() (no <loc_>, so no y0) would
#: relocate it to the front of that page, and joining pages would then
#: produce two ADJACENT <page_break> tags — the exact "double page_break"
#: pathology this page-splitting rewrite exists to eliminate (cf. "TN -
#: Fortune et revenu acquis sous forme de rente" in
#: docs/rapport_erreur_resolution.md).
_PAGE_BREAK_RE = re.compile(r"\n?<page_break>\n?")


def _page_body(doctags_doc: DoclingDocument, page_no: int) -> str:
    """One page's content, unwrapped and with every page_break marker removed."""
    exported = doctags_doc.export_to_doctags(pages={page_no})
    inner = _DOCTAG_WRAPPER_RE.sub("", exported)
    return _PAGE_BREAK_RE.sub("\n", inner).strip()


def reorder_doctags_from_json(json_path: Path, output_path: Path) -> None:
    """Rebuild ``<doc>_reordered.doctags`` directly from ``<doc>.json``.

    Same output contract as ``reorder_doctags.reorder_doctags()`` (a single
    ``<doctag>...</doctag>`` block, pages joined by a lone ``<page_break>``),
    so it is a drop-in replacement for the downstream steps that already
    consume ``_reordered.doctags``.

    :param json_path: path to ``<doc>.json`` (Docling's native export,
        produced by docling-extract alongside the ``.doctags``).
    :param output_path: path to write the rebuilt ``_reordered.doctags`` to.
    """
    doctags_doc = DoclingDocument.load_from_json(json_path)
    pages = [_page_body(doctags_doc, page_no) for page_no in range(1, doctags_doc.num_pages() + 1)]
    body = "\n<page_break>\n".join(pages)
    output_path.write_text(f"<doctag>\n{body}\n</doctag>\n", encoding="utf-8")


class ReorderDoctagsV2Step(ReorderDoctagsStep):
    """Opt-in variant of ``reorder-doctags`` sourced from the JSON, not the
    ``.doctags`` text. Inherits ``name``/``outputs()`` from the real step so it
    slots into the pipeline the same way; only ``inputs()`` and ``execute()``
    differ, since it reads ``<doc>.json`` instead of ``<doc>.doctags``.
    """

    def inputs(self, ctx: PipelineContext) -> list[Path]:
        return [ctx.workspace.docling_json]

    def execute(self, ctx: PipelineContext) -> StepResult:
        from ..core.step import StepResult, StepStatus

        input_path = ctx.workspace.docling_json
        output_path = ctx.workspace.reordered_doctags
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _log.info("Input : %s", input_path)
        _log.info("Output: %s", output_path)
        try:
            reorder_doctags_from_json(input_path, output_path)
        except Exception as exc:
            _log.exception("Error while reordering %s", input_path.name)
            raise StepFailed(f"reorder-doctags (v2) failed on {input_path.name}: {exc}") from exc
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
