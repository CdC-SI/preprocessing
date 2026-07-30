"""Rapport de comparaison baseline vs pipeline, en HTML — **sans appel VLM**.

Variante déterministe de ``compare_baseline_report.py`` : mêmes entrées
(``baseline_results.csv`` + ``global_summary.csv``), même calcul de delta
— les trois fonctions d'agrégation sont importées telles quelles, il n'y a
qu'une seule source de vérité pour les chiffres — mais la sortie est une page
HTML autonome au lieu d'un markdown + PNG, et **aucun VLM n'est sollicité**.

Deux conséquences pratiques :

- rejouable hors ligne et reproductible au bit près (le verdict VLM, lui, varie
  d'un run à l'autre : pas de cache, contrainte C1) ;
- les graphiques sont du SVG écrit à la main, donc l'extra ``viz``
  (matplotlib) n'est **pas** nécessaire — seul ``eval`` l'est, pour les
  métriques amont.

La page est autoportante : aucun CSS, script, police ni image externe. Elle
s'ouvre par double-clic et suit le thème clair/sombre du système.

Usage :
    uv run python -m afac_preprocessing.pipeline_baseline.comparison_report_html
    uv run python -m afac_preprocessing.pipeline_baseline.comparison_report_html --canonical-k 10
"""

from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..settings import _find_project_root
from .compare_baseline_report import (
    _METRICS,
    build_comparison,
    load_baseline_means,
    load_pipeline_means,
)

_log = logging.getLogger(__name__)

project_root = _find_project_root

DEFAULT_BASELINE_RESULTS = project_root() / "data" / "baseline_evaluation" / "baseline_results.csv"
DEFAULT_PIPELINE_SUMMARY = project_root() / "data" / "pipeline_evaluation" / "global_summary.csv"
DEFAULT_OUTPUT_HTML = project_root() / "data" / "baseline_evaluation" / "comparison_report.html"

TOP_KS_DEFAULT = [1, 3, 5, 10, 20]
CANONICAL_K_DEFAULT = 5

METRIC_LABELS = {
    "recall": "Recall",
    "precision": "Precision",
    "ndcg": "nDCG",
    "mrr": "MRR",
}

# Marques (marks-and-anatomy) : barres ≤24px, extrémité arrondie 4px, écart de
# 2px couleur surface entre barres adjacentes, lignes 2px, marqueurs r≥4 avec
# anneau de 2px, grille en filet 1px pleine et discrète.
BAR_MAX_THICKNESS = 24.0
BAR_GAP = 2.0
BAR_RADIUS = 4.0


# --- helpers SVG -----------------------------------------------------------


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def _fmt_delta(value: float) -> str:
    return f"{'+' if value > 0 else ''}{value:.3f}"


def _bar_path(x: float, y: float, width: float, height: float, *, radius: float = BAR_RADIUS) -> str:
    """Barre verticale : extrémité haute arrondie, pied carré sur la ligne de base."""
    if height <= 0:
        return ""
    r = min(radius, width / 2, height)
    return (
        f"M{x:.2f},{y + height:.2f} "
        f"L{x:.2f},{y + r:.2f} "
        f"Q{x:.2f},{y:.2f} {x + r:.2f},{y:.2f} "
        f"L{x + width - r:.2f},{y:.2f} "
        f"Q{x + width:.2f},{y:.2f} {x + width:.2f},{y + r:.2f} "
        f"L{x + width:.2f},{y + height:.2f} Z"
    )


def _hbar_path(x: float, y: float, width: float, height: float, *, to_right: bool) -> str:
    """Barre horizontale divergente : extrémité arrondie côté valeur, carrée côté zéro."""
    if width <= 0:
        return ""
    r = min(BAR_RADIUS, height / 2, width)
    if to_right:
        return (
            f"M{x:.2f},{y:.2f} L{x + width - r:.2f},{y:.2f} "
            f"Q{x + width:.2f},{y:.2f} {x + width:.2f},{y + r:.2f} "
            f"L{x + width:.2f},{y + height - r:.2f} "
            f"Q{x + width:.2f},{y + height:.2f} {x + width - r:.2f},{y + height:.2f} "
            f"L{x:.2f},{y + height:.2f} Z"
        )
    return (
        f"M{x + width:.2f},{y:.2f} L{x + r:.2f},{y:.2f} "
        f"Q{x:.2f},{y:.2f} {x:.2f},{y + r:.2f} "
        f"L{x:.2f},{y + height - r:.2f} "
        f"Q{x:.2f},{y + height:.2f} {x + r:.2f},{y + height:.2f} "
        f"L{x + width:.2f},{y + height:.2f} Z"
    )


def _y_ticks(vmax: float, count: int = 4) -> list[float]:
    """Graduations rondes de 0 à vmax (les métriques vivent dans [0, 1])."""
    step = 0.25 if vmax > 0.5 else 0.1
    ticks, value = [], 0.0
    while value <= vmax + 1e-9 and len(ticks) <= count + 2:
        ticks.append(round(value, 4))
        value += step
    if ticks[-1] < vmax:
        ticks.append(round(ticks[-1] + step, 4))
    return ticks


# --- graphiques ------------------------------------------------------------


def svg_global_bars(global_means: dict[str, dict[str, float]], canonical_k: int) -> str:
    """Barres groupées : les 4 métriques @k, baseline contre pipeline.

    Job = distinguer deux séries → couleur catégorielle (slots 1 et 2).
    """
    width, height = 720.0, 300.0
    pad_l, pad_r, pad_t, pad_b = 48.0, 16.0, 16.0, 44.0
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    vmax = max(
        max(global_means[m]["baseline"], global_means[m]["pipeline"]) for m in _METRICS
    )
    ticks = _y_ticks(max(vmax, 0.2))
    scale_max = ticks[-1]

    def y_of(value: float) -> float:
        return pad_t + plot_h * (1 - value / scale_max)

    band = plot_w / len(_METRICS)
    bar_w = min(BAR_MAX_THICKNESS, (band - BAR_GAP) / 2 - 8)

    parts: list[str] = []
    for tick in ticks:
        y = y_of(tick)
        parts.append(
            f'<line class="grid" x1="{pad_l:.1f}" y1="{y:.2f}" x2="{width - pad_r:.1f}" y2="{y:.2f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{pad_l - 8:.1f}" y="{y + 4:.2f}" text-anchor="end">{tick:g}</text>'
        )

    for idx, metric in enumerate(_METRICS):
        centre = pad_l + band * (idx + 0.5)
        for offset, arm in ((-1, "baseline"), (1, "pipeline")):
            value = global_means[metric][arm]
            x = centre + offset * (BAR_GAP / 2) - (bar_w if offset < 0 else 0)
            y = y_of(value)
            tip = f"{METRIC_LABELS[metric]}@{canonical_k} — {arm} : {_fmt(value)}"
            parts.append(
                f'<path class="mark bar-{arm}" d="{_bar_path(x, y, bar_w, pad_t + plot_h - y)}" '
                f'data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></path>'
            )
            # Label direct sur la tête de colonne : 8 barres seulement, lisible.
            parts.append(
                f'<text class="value" x="{x + bar_w / 2:.2f}" y="{y - 6:.2f}" '
                f'text-anchor="middle">{_fmt(value)}</text>'
            )
        parts.append(
            f'<text class="axis" x="{centre:.2f}" y="{height - pad_b + 22:.1f}" '
            f'text-anchor="middle">{_esc(METRIC_LABELS[metric])}@{canonical_k}</text>'
        )

    parts.append(
        f'<line class="baseline-axis" x1="{pad_l:.1f}" y1="{pad_t + plot_h:.2f}" '
        f'x2="{width - pad_r:.1f}" y2="{pad_t + plot_h:.2f}"/>'
    )
    return (
        f'<svg viewBox="0 0 {width:g} {height:g}" role="img" '
        f'aria-label="Moyennes des métriques à k={canonical_k}, baseline contre pipeline">'
        + "".join(parts)
        + "</svg>"
    )


def svg_metric_by_k(comparison: pd.DataFrame, metric: str, top_ks: list[int]) -> str:
    """Une métrique en fonction de k — deux lignes (baseline, pipeline).

    Facetté en petits multiples (une carte par métrique) plutôt qu'un seul
    graphique à huit lignes : au-delà de ~4 séries qui convergent, les petits
    multiples sont la bonne réponse.
    """
    width, height = 340.0, 190.0
    pad_l, pad_r, pad_t, pad_b = 40.0, 46.0, 14.0, 32.0
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b

    series = {
        arm: [float(comparison[f"{metric}@{k}_{arm}"].mean()) for k in top_ks]
        for arm in ("baseline", "pipeline")
    }
    vmax = max(max(series["baseline"]), max(series["pipeline"]), 0.2)
    ticks = _y_ticks(vmax)
    scale_max = ticks[-1]

    def x_of(i: int) -> float:
        return pad_l + (plot_w * i / (len(top_ks) - 1) if len(top_ks) > 1 else plot_w / 2)

    def y_of(value: float) -> float:
        return pad_t + plot_h * (1 - value / scale_max)

    parts: list[str] = []
    for tick in ticks:
        y = y_of(tick)
        parts.append(
            f'<line class="grid" x1="{pad_l:.1f}" y1="{y:.2f}" x2="{pad_l + plot_w:.1f}" y2="{y:.2f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{pad_l - 6:.1f}" y="{y + 4:.2f}" text-anchor="end">{tick:g}</text>'
        )

    for arm in ("baseline", "pipeline"):
        points = " ".join(f"{x_of(i):.2f},{y_of(v):.2f}" for i, v in enumerate(series[arm]))
        parts.append(f'<polyline class="line line-{arm}" points="{points}"/>')
        for i, value in enumerate(series[arm]):
            tip = f"{METRIC_LABELS[metric]}@{top_ks[i]} — {arm} : {_fmt(value)}"
            parts.append(
                f'<circle class="dot dot-{arm}" cx="{x_of(i):.2f}" cy="{y_of(value):.2f}" r="4" '
                f'data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></circle>'
            )
        # Label direct au dernier point seulement (jamais une valeur par point).
        last = len(series[arm]) - 1
        parts.append(
            f'<text class="value" x="{x_of(last) + 8:.2f}" y="{y_of(series[arm][last]) + 4:.2f}">'
            f"{_fmt(series[arm][last])}</text>"
        )

    for i, k in enumerate(top_ks):
        parts.append(
            f'<text class="axis" x="{x_of(i):.2f}" y="{height - pad_b + 20:.1f}" '
            f'text-anchor="middle">k={k}</text>'
        )
    parts.append(
        f'<line class="baseline-axis" x1="{pad_l:.1f}" y1="{pad_t + plot_h:.2f}" '
        f'x2="{pad_l + plot_w:.1f}" y2="{pad_t + plot_h:.2f}"/>'
    )
    return (
        f'<svg viewBox="0 0 {width:g} {height:g}" role="img" '
        f'aria-label="{_esc(METRIC_LABELS[metric])} en fonction de k">'
        + "".join(parts)
        + "</svg>"
    )


def svg_per_doc_delta(comparison: pd.DataFrame, canonical_k: int) -> str:
    """Delta nDCG@k par document — barres divergentes autour de zéro.

    Job = polarité (au-dessus / en-dessous d'une ligne de base) → palette
    divergente : deux pôles chaud/froid et un milieu neutre.
    """
    column = f"ndcg@{canonical_k}_delta"
    rows = comparison[["doc_name", column]].sort_values(column, ascending=False)
    docs = rows["doc_name"].tolist()
    values = [float(v) for v in rows[column]]
    if not docs:
        return '<p class="empty">Aucun document commun aux deux arms.</p>'

    row_h, gap = 22.0, 6.0
    width = 720.0
    pad_l, pad_r, pad_t, pad_b = 210.0, 60.0, 8.0, 30.0
    plot_w = width - pad_l - pad_r
    height = pad_t + pad_b + len(docs) * (row_h + gap)

    vmax = max((abs(v) for v in values), default=0.1) or 0.1
    centre = pad_l + plot_w / 2

    def x_of(value: float) -> float:
        return centre + (plot_w / 2) * (value / vmax)

    parts: list[str] = []
    for idx, (doc, value) in enumerate(zip(docs, values, strict=True)):
        y = pad_t + idx * (row_h + gap)
        bar_h = min(BAR_MAX_THICKNESS, row_h)
        positive = value > 0
        x_start, x_end = (centre, x_of(value)) if positive else (x_of(value), centre)
        klass = "delta-worse" if positive else "delta-better"
        meaning = "le prétraitement dégrade" if positive else "le prétraitement améliore"
        tip = f"{doc} — delta nDCG@{canonical_k} : {_fmt_delta(value)} ({meaning})"
        parts.append(
            f'<path class="mark {klass}" '
            f'd="{_hbar_path(x_start, y, abs(x_end - x_start), bar_h, to_right=positive)}" '
            f'data-tip="{_esc(tip)}"><title>{_esc(tip)}</title></path>'
        )
        label = doc if len(doc) <= 30 else doc[:29] + "…"
        parts.append(
            f'<text class="axis doc" x="{pad_l - 12:.1f}" y="{y + bar_h / 2 + 4:.2f}" '
            f'text-anchor="end">{_esc(label)}<title>{_esc(doc)}</title></text>'
        )
        anchor = "start" if positive else "end"
        x_text = x_of(value) + (8 if positive else -8)
        parts.append(
            f'<text class="value" x="{x_text:.2f}" y="{y + bar_h / 2 + 4:.2f}" '
            f'text-anchor="{anchor}">{_fmt_delta(value)}</text>'
        )

    parts.append(
        f'<line class="zero-axis" x1="{centre:.2f}" y1="{pad_t:.1f}" '
        f'x2="{centre:.2f}" y2="{height - pad_b:.2f}"/>'
    )
    parts.append(
        f'<text class="axis" x="{centre:.2f}" y="{height - pad_b + 20:.1f}" '
        f'text-anchor="middle">0 — pas de différence</text>'
    )
    return (
        f'<svg viewBox="0 0 {width:g} {height:.0f}" role="img" '
        f'aria-label="Delta nDCG@{canonical_k} par document">' + "".join(parts) + "</svg>"
    )


# --- page ------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
.viz-root {
  color-scheme: light;
  --surface-1: #fcfcfb; --plane: #f9f9f7;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-baseline: #2a78d6; --series-pipeline: #eb6834;
  --div-worse: #e34948; --div-better: #2a78d6; --div-mid: #f0efec;
  --good-ink: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    color-scheme: dark;
    --surface-1: #1a1a19; --plane: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-baseline: #3987e5; --series-pipeline: #d95926;
    --div-worse: #e66767; --div-better: #3987e5; --div-mid: #383835;
    --good-ink: #0ca30c;
  }
}
:root[data-theme="dark"] .viz-root {
  color-scheme: dark;
  --surface-1: #1a1a19; --plane: #0d0d0d;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-baseline: #3987e5; --series-pipeline: #d95926;
  --div-worse: #e66767; --div-better: #3987e5; --div-mid: #383835;
  --good-ink: #0ca30c;
}

* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px 20px 64px;
  background: var(--plane); color: var(--text-primary);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif;
}
.viz-root { max-width: 1000px; margin: 0 auto; }
h1 { font-size: 25px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 17px; margin: 40px 0 6px; }
.sub { color: var(--text-secondary); margin: 0 0 8px; }
.note { color: var(--text-secondary); font-size: 13.5px; margin: 0 0 16px; }
.card {
  background: var(--surface-1); border: 1px solid var(--border);
  border-radius: 12px; padding: 18px 18px 12px; margin-top: 12px;
}
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); gap: 12px; margin-top: 16px; }
.kpi { background: var(--surface-1); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; }
.kpi .label { color: var(--text-secondary); font-size: 13px; }
.kpi .value { font-size: 30px; font-weight: 600; margin-top: 2px; }
.kpi .delta { font-size: 13.5px; margin-top: 2px; color: var(--text-secondary); }
.kpi .delta b { font-weight: 600; }
.hero { font-size: 46px; font-weight: 650; letter-spacing: -0.02em; margin: 4px 0 0; }
.grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 12px; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; margin: 2px 0 10px; font-size: 13.5px; color: var(--text-secondary); }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.swatch { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid { stroke: var(--grid); stroke-width: 1; }
.baseline-axis, .zero-axis { stroke: var(--axis); stroke-width: 1; }
.tick, .axis { fill: var(--muted); font-size: 11.5px; }
.axis.doc { fill: var(--text-secondary); font-size: 12px; }
.value { fill: var(--text-secondary); font-size: 11.5px; font-variant-numeric: tabular-nums; }
.bar-baseline { fill: var(--series-baseline); }
.bar-pipeline { fill: var(--series-pipeline); }
.delta-worse { fill: var(--div-worse); }
.delta-better { fill: var(--div-better); }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
.line-baseline { stroke: var(--series-baseline); }
.line-pipeline { stroke: var(--series-pipeline); }
.dot { stroke: var(--surface-1); stroke-width: 2; }
.dot-baseline { fill: var(--series-baseline); }
.dot-pipeline { fill: var(--series-pipeline); }
.mark, .dot { transition: opacity .12s ease; }
.mark:hover, .dot:hover { opacity: .78; cursor: default; }
.facet h3 { font-size: 13.5px; margin: 0 0 2px; color: var(--text-secondary); font-weight: 600; }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; font-variant-numeric: tabular-nums; }
th, td { text-align: right; padding: 7px 10px; border-bottom: 1px solid var(--border); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; white-space: normal; }
thead th { color: var(--text-secondary); font-weight: 600; }
.scroll { overflow-x: auto; }
.pos { color: var(--div-worse); }
.neg { color: var(--good-ink); }
#tip {
  position: fixed; pointer-events: none; opacity: 0; transition: opacity .1s;
  background: var(--text-primary); color: var(--surface-1);
  padding: 6px 9px; border-radius: 7px; font-size: 12.5px; max-width: 320px; z-index: 9;
}
footer { color: var(--muted); font-size: 12.5px; margin-top: 40px; }
@media print { .card, .kpi { break-inside: avoid; } }
"""

_JS = """
(function () {
  var tip = document.getElementById('tip');
  document.addEventListener('mouseover', function (e) {
    var t = e.target.closest('[data-tip]');
    if (!t) return;
    tip.textContent = t.getAttribute('data-tip');
    tip.style.opacity = '1';
  });
  document.addEventListener('mousemove', function (e) {
    if (tip.style.opacity !== '1') return;
    var x = e.clientX + 14, y = e.clientY + 16;
    var r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - 14;
    if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - 16;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  });
  document.addEventListener('mouseout', function (e) {
    if (e.target.closest('[data-tip]')) tip.style.opacity = '0';
  });
})();
"""


def _global_means(comparison: pd.DataFrame, canonical_k: int) -> dict[str, dict[str, float]]:
    return {
        metric: {
            arm: float(comparison[f"{metric}@{canonical_k}_{arm}"].mean())
            for arm in ("baseline", "pipeline", "delta")
        }
        for metric in _METRICS
    }


def _verdict(global_means: dict[str, dict[str, float]]) -> tuple[str, str]:
    """Verdict calculé, pas rédigé : signe du delta nDCG moyen."""
    delta = global_means["ndcg"]["delta"]
    if abs(delta) < 0.005:
        return "Équivalent", "Les deux représentations se valent sur ce corpus."
    if delta > 0:
        return (
            "La baseline fait mieux",
            "Sur ce corpus, le prétraitement dégrade le retrieval.",
        )
    return (
        "Le pipeline fait mieux",
        "Sur ce corpus, le prétraitement améliore le retrieval.",
    )


def render_html(comparison: pd.DataFrame, top_ks: list[int], canonical_k: int) -> str:
    means = _global_means(comparison, canonical_k)
    headline, explanation = _verdict(means)
    n_docs = len(comparison)
    n_questions = int(comparison["n_questions_baseline"].sum()) if "n_questions_baseline" in comparison else 0

    kpis = []
    for metric in _METRICS:
        delta = means[metric]["delta"]
        klass = "pos" if delta > 0 else "neg"
        arrow = "▲ baseline" if delta > 0 else "▼ pipeline"
        kpis.append(
            f'<div class="kpi"><div class="label">{_esc(METRIC_LABELS[metric])}@{canonical_k} '
            f'— pipeline</div><div class="value">{_fmt(means[metric]["pipeline"])}</div>'
            f'<div class="delta">baseline {_fmt(means[metric]["baseline"])} · '
            f'delta <b class="{klass}">{_fmt_delta(delta)}</b> {arrow}</div></div>'
        )

    legend = (
        '<div class="legend">'
        '<span><i class="swatch" style="background:var(--series-baseline)"></i>Baseline (docling brut)</span>'
        '<span><i class="swatch" style="background:var(--series-pipeline)"></i>Pipeline (prétraité)</span>'
        "</div>"
    )
    legend_delta = (
        '<div class="legend">'
        '<span><i class="swatch" style="background:var(--div-better)"></i>Le prétraitement améliore (delta &lt; 0)</span>'
        '<span><i class="swatch" style="background:var(--div-worse)"></i>Le prétraitement dégrade (delta &gt; 0)</span>'
        "</div>"
    )

    facets = "".join(
        f'<div class="card facet"><h3>{_esc(METRIC_LABELS[m])} en fonction de k</h3>'
        f"{svg_metric_by_k(comparison, m, top_ks)}</div>"
        for m in _METRICS
    )

    head = "".join(
        f"<th>{_esc(METRIC_LABELS[m])} base.</th><th>{_esc(METRIC_LABELS[m])} pipe.</th>"
        f"<th>Δ</th>"
        for m in _METRICS
    )
    body_rows = []
    for _, row in comparison.sort_values(f"ndcg@{canonical_k}_delta", ascending=False).iterrows():
        cells = []
        for metric in _METRICS:
            delta = float(row[f"{metric}@{canonical_k}_delta"])
            cells.append(
                f'<td>{_fmt(row[f"{metric}@{canonical_k}_baseline"])}</td>'
                f'<td>{_fmt(row[f"{metric}@{canonical_k}_pipeline"])}</td>'
                f'<td class="{"pos" if delta > 0 else "neg"}">{_fmt_delta(delta)}</td>'
            )
        body_rows.append(f'<tr><td>{_esc(row["doc_name"])}</td>{"".join(cells)}</tr>')

    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Comparaison baseline vs pipeline — AFAC</title>
<style>{_CSS}</style></head>
<body><div class="viz-root">
<h1>Baseline docling brut vs pipeline de prétraitement</h1>
<p class="sub">{n_docs} document(s), {n_questions} question(s) HyQ · généré le
{datetime.now().strftime("%Y-%m-%d %H:%M")} · <strong>sans appel VLM</strong></p>

<h2>Verdict</h2>
<p class="hero">{_esc(headline)}</p>
<p class="note">{_esc(explanation)} Le verdict est <em>calculé</em> — c'est le signe du
delta nDCG@{canonical_k} moyen, pas une rédaction de modèle. <strong>Delta = baseline −
pipeline</strong> : positif ⇒ la baseline fait mieux, donc le prétraitement dégrade ;
négatif ⇒ le prétraitement améliore.</p>
<div class="kpis">{"".join(kpis)}</div>

<h2>Moyennes globales à k={canonical_k}</h2>
<p class="note">Mêmes questions HyQ des deux côtés : seule la représentation du document change.</p>
<div class="card">{legend}{svg_global_bars(means, canonical_k)}</div>

<h2>Évolution en fonction de k</h2>
<p class="note">Une carte par métrique — les deux séries partagent la même échelle dans chaque carte.</p>
{legend}
<div class="grid2">{facets}</div>

<h2>Par document — delta nDCG@{canonical_k}</h2>
<p class="note">Trié du plus dégradé au plus amélioré. Les documents en tête sont ceux à
inspecter en premier (longueur du markdown enrichi, descriptions d'images répétées d'un
document à l'autre, tables converties en JSON-lines).</p>
<div class="card">{legend_delta}{svg_per_doc_delta(comparison, canonical_k)}</div>

<h2>Tableau complet (k={canonical_k})</h2>
<div class="card scroll"><table>
<thead><tr><th>Document</th>{head}</tr></thead>
<tbody>{"".join(body_rows)}</tbody></table></div>

<footer>Généré par <code>comparison_report_html.py</code> — page autoportante,
aucune ressource externe. Les chiffres viennent de <code>baseline_results.csv</code> et
<code>global_summary.csv</code>, via les mêmes fonctions d'agrégation que le rapport
markdown.</footer>
</div><div id="tip"></div><script>{_JS}</script></body></html>
"""


# --- CLI -------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rapport HTML de comparaison baseline vs pipeline — sans appel VLM.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--baseline-results", type=Path, default=DEFAULT_BASELINE_RESULTS)
    parser.add_argument("--pipeline-summary", type=Path, default=DEFAULT_PIPELINE_SUMMARY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_HTML)
    parser.add_argument("--top-ks", default=",".join(map(str, TOP_KS_DEFAULT)))
    parser.add_argument("--canonical-k", type=int, default=CANONICAL_K_DEFAULT)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    top_ks = [int(k) for k in str(args.top_ks).split(",") if str(k).strip()]
    if args.canonical_k not in top_ks:
        _log.error("--canonical-k %d absent de --top-ks %s", args.canonical_k, top_ks)
        sys.exit(1)

    for path in (args.baseline_results, args.pipeline_summary):
        if not path.exists():
            _log.error(
                "Fichier introuvable : %s — lancer d'abord single_docling_baseline "
                "puis evaluate_all_docs.",
                path,
            )
            sys.exit(1)

    comparison = build_comparison(
        load_baseline_means(args.baseline_results, top_ks),
        load_pipeline_means(args.pipeline_summary, top_ks),
        top_ks,
    )
    if comparison.empty:
        _log.error("Aucun document commun à la baseline et au pipeline.")
        sys.exit(1)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(comparison, top_ks, args.canonical_k), encoding="utf-8")
    _log.info("Rapport HTML écrit : %s (%d document(s))", args.output, len(comparison))


if __name__ == "__main__":
    main()
