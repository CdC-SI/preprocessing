"""reorder-doctags v3 — hybride entre v1 et v2.

Not wired into the default pipeline — see ``core/registry.build_default_steps``
for how ``--fixed-tables`` opts into a sibling variant; the same mechanism
would apply here once this is validated.

Pourquoi un v3
--------------
- v1 (``reorder_doctags.py``) reconstruit les frontieres de page en comptant
  des marqueurs ``<page_footer>``/``<page_break>`` dans le texte a plat, ce
  qui peut fusionner deux pages a tort (verifie sur "TN - Fortune et revenu
  acquis sous forme de rente" : une case a cocher de la page 2 s'est retrouvee
  triee apres un paragraphe de la page 3). Mais il TRIE chaque page par
  (y0, x0), ce que v2 ne fait pas.
- v2 (``reorder_doctags_v2.py``) source les frontieres de page depuis
  ``prov[].page_no`` du JSON natif de Docling — fiable, verifie sans aucune
  regression sur 136 documents. Mais il se contente de l'ordre "naturel" que
  Docling attribue par page (``export_to_doctags(pages={n})``), qui n'est PAS
  garanti trie par position : verifie sur "Adhésion traitement", un bandeau
  d'en-tete a 2 colonnes (logo a gauche, texte institutionnel a droite) sort
  avec le logo (y0=20) place APRES les deux lignes de texte (y0=21, y0=33).

v3 prend le meilleur des deux : les frontieres de page de v2 (fiables), puis
le tri par position de v1 (``parse_blocks`` + ``sort_page`` + ``render_blocks``,
reutilises tels quels, pas reimplementes) applique a l'interieur de CHAQUE
page ainsi correctement delimitee.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from docling_core.types.doc.document import DoclingDocument

from ..exceptions import StepFailed
from .reorder_doctags import parse_blocks, render_blocks, sort_page
from .reorder_doctags_v2 import ReorderDoctagsV2Step, _page_body

if TYPE_CHECKING:
    from ..context import PipelineContext
    from ..core.step import StepResult

_log = logging.getLogger(__name__)


def reorder_doctags_v3(json_path: Path, output_path: Path) -> None:
    """Rebuild ``<doc>_reordered.doctags`` from ``<doc>.json``, avec un tri
    (y0, x0) applique par page (comme v1) sur des pages correctement
    delimitees (comme v2).

    Meme contrat de sortie que v1 et v2 : un seul bloc ``<doctag>...</doctag>``,
    pages jointes par un unique ``<page_break>``.
    """
    doctags_doc = DoclingDocument.load_from_json(json_path)
    pages = []
    for page_no in range(1, doctags_doc.num_pages() + 1):
        body = _page_body(doctags_doc, page_no)
        sorted_blocks = sort_page(parse_blocks(body))
        pages.append(render_blocks(sorted_blocks))

    body = "\n<page_break>\n".join(pages)
    output_path.write_text(f"<doctag>\n{body}\n</doctag>\n", encoding="utf-8")


class ReorderDoctagsV3Step(ReorderDoctagsV2Step):
    """Opt-in variant of ``reorder-doctags``: frontieres de page issues du
    JSON (v2), tri (y0, x0) intra-page issu de v1. Herite de ``name``,
    ``inputs()`` et ``outputs()`` de ``ReorderDoctagsV2Step``.
    """

    def execute(self, ctx: PipelineContext) -> StepResult:
        from ..core.step import StepResult, StepStatus

        input_path = ctx.workspace.docling_json
        output_path = ctx.workspace.reordered_doctags
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _log.info("Input : %s", input_path)
        _log.info("Output: %s", output_path)
        try:
            reorder_doctags_v3(input_path, output_path)
        except Exception as exc:
            _log.exception("Error while reordering %s", input_path.name)
            raise StepFailed(f"reorder-doctags (v3) failed on {input_path.name}: {exc}") from exc
        return StepResult(StepStatus.OK, outputs=self.outputs(ctx))
