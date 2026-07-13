"""
markdown_utils.py — Shared Markdown post-processing.
"""


def apply_markdown_transforms(text: str) -> str:
    """
    Entry point kept for compatibility with existing calls in the pipeline.
    Custom tag transformations (color, underline) are now handled
    directly by the VLM in stage 10.

    :param text: Markdown text
    :return: unchanged text
    """
    return text
