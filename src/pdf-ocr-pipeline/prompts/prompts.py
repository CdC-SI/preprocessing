OCR_PROMPT = """In the context of ingesting user documents into a RAG vector database, your task is to OCR and extract all informative content from the provided document, including text, tables, and figures.

For tables, extract them as List[Dict] (JSONL style): each row is a dict with the column name as key, the value as the value for that row and column. Add any relevant keys if necessary.

For images, screenshots, charts, or figures, do not attempt pixel-level OCR; instead, provide a concise, descriptive alt-text summary that captures the semantic information relevant for retrieval.

You may ignore non-informative elements such as logos, decorative graphics, headers, footers, navigation menus, and branding.

For legal and regulatory documents, ignore footnotes containing only statutory citations, article cross-references, or amendment histories; keep any footnote that carries substantive content.

Output only the extracted content, without commentary or explanations.

Format the output in Markdown."""

LLM_PROMPT = """In the context of ingesting user documents into a RAG vector database, your task is to:
- detect the language of the document (if mix of languages, select the principal one): fr/it/de/en/etc.
- generate a 2-3 sentence summary of the document in it's language

Extract only the required fields. Do not add any commentary or explanations.

Document:
{doc}"""