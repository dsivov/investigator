"""Manual-source ingestion: turn user-supplied URLs and PDF files into the
same article-dict shape the GNews fetcher produces, so they flow through the
identical NER -> events -> graph pipeline.

The pipeline consumes article dicts with these keys (see
`evaluate_investigator_server.fetch_news` / `build_payload`):

    {"title", "publisher", "real_url", "published_date", "text", "error"}

Manual sources are user-chosen, so they are *always included* -- they skip the
GNews title-rerank cutoff. They still pass the per-evidence relevance gate
against the domain hypothesis downstream, like any other article. Each manual
record carries `provenance="manual"` so the report / Sources tab can flag it.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from newspaper import Article


def _empty_record(title: str, publisher: str, real_url: str | None) -> dict:
    return {
        "title": title,
        "publisher": publisher,
        "real_url": real_url,
        "published_date": None,
        "text": "",
        "error": None,
        "provenance": "manual",
    }


def ingest_url(url: str) -> dict:
    """Fetch + extract a single URL with newspaper3k (the same extractor the
    GNews path uses for article bodies). Returns one article dict; `error` is
    populated and `text` left empty on failure."""
    rec = _empty_record(title=url, publisher="", real_url=url)
    try:
        art = Article(url)
        art.download()
        art.parse()
        rec["text"] = (art.text or "").strip()
        if art.title:
            rec["title"] = art.title.strip()
        # newspaper exposes the source netloc; fall back to the host.
        rec["publisher"] = (art.source_url or "").replace("https://", "").replace(
            "http://", "").split("/")[0]
        if art.publish_date:
            rec["published_date"] = art.publish_date.isoformat()
        if not rec["text"]:
            rec["error"] = "empty body (extraction returned 0 chars)"
    except Exception as e:  # noqa: BLE001 -- surface any fetch/parse failure as a record error
        rec["error"] = f"url-fetch: {type(e).__name__}: {str(e)[:200]}"
    return rec


def _extract_pdf_text(path: Path) -> str:
    """Extract text from a PDF. Prefer PyMuPDF (fitz); fall back to pdfminer."""
    try:
        import fitz  # PyMuPDF

        with fitz.open(path) as doc:
            return "\n".join(page.get_text() for page in doc).strip()
    except Exception:  # noqa: BLE001 -- fall back to pdfminer
        from pdfminer.high_level import extract_text

        return (extract_text(str(path)) or "").strip()


def ingest_pdf(path: str | Path) -> dict:
    """Extract text from a local PDF file into an article dict. Title is the
    first non-empty line of the document, falling back to the file stem."""
    p = Path(path)
    rec = _empty_record(title=p.stem, publisher="Uploaded PDF", real_url=str(p))
    try:
        text = _extract_pdf_text(p)
        rec["text"] = text
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        if first_line:
            rec["title"] = first_line[:200]
        rec["published_date"] = datetime.fromtimestamp(p.stat().st_mtime).isoformat()
        if not rec["text"]:
            rec["error"] = "empty body (PDF extraction returned 0 chars)"
    except Exception as e:  # noqa: BLE001
        rec["error"] = f"pdf-extract: {type(e).__name__}: {str(e)[:200]}"
    return rec


# A single manual document is one big text value. The engine's JSON chunker
# splits payloads by *structure*, not by breaking a long leaf string, so one
# huge record becomes a single oversized NER chunk that the LLM call fails on
# (its failure is silently tolerated -> zero entities). GNews articles dodge
# this only because a payload carries dozens of small records. So we pre-split
# a long manual document into several NER-sized records: one document then
# behaves like several small articles and chunks cleanly.
_MAX_RECORD_CHARS = 3000


def _split_text(text: str, max_chars: int = _MAX_RECORD_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    cur = ""
    for para in text.split("\n"):
        if len(para) > max_chars:
            # Flush, then hard-split an oversized paragraph.
            if cur:
                parts.append(cur)
                cur = ""
            for i in range(0, len(para), max_chars):
                parts.append(para[i:i + max_chars])
            continue
        if cur and len(cur) + len(para) + 1 > max_chars:
            parts.append(cur)
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


def _expand_record(rec: dict) -> list[dict]:
    """Split a record's text into NER-sized part-records (sharing title / url /
    publisher / provenance). Short records pass through unchanged."""
    parts = _split_text(rec.get("text") or "")
    if len(parts) <= 1:
        return [rec]
    out = []
    for piece in parts:
        r = dict(rec)
        r["text"] = piece
        out.append(r)
    return out


def ingest_sources(urls: list[str] | None = None,
                   pdfs: list[str] | None = None,
                   *, verbose: bool = False) -> list[dict]:
    """Ingest a batch of manual sources into article dicts. Long documents are
    split into several NER-sized part-records. Records that fail extraction
    (empty `text`) are dropped -- their `error` is printed when `verbose`."""
    records: list[dict] = []
    for url in (urls or []):
        rec = ingest_url(url)
        if verbose:
            n = len(rec.get("text") or "")
            print(f"  [url] {url[:70]} -> {n:,} chars"
                  + (f"  ERROR: {rec['error']}" if rec.get("error") else ""))
        records.append(rec)
    for pdf in (pdfs or []):
        rec = ingest_pdf(pdf)
        if verbose:
            n = len(rec.get("text") or "")
            print(f"  [pdf] {Path(pdf).name[:70]} -> {n:,} chars"
                  + (f"  ERROR: {rec['error']}" if rec.get("error") else ""))
        records.append(rec)
    usable = [r for r in records if r.get("text")]
    expanded = [part for r in usable for part in _expand_record(r)]
    if verbose and len(expanded) != len(usable):
        print(f"  [split] {len(usable)} document(s) -> {len(expanded)} NER-sized record(s)")
    return expanded


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Ingest URLs / PDFs into article dicts.")
    ap.add_argument("--url", action="append", default=[])
    ap.add_argument("--pdf", action="append", default=[])
    ap.add_argument("--json", action="store_true", help="dump the article dicts")
    a = ap.parse_args()
    recs = ingest_sources(a.url, a.pdf, verbose=True)
    print(f"\n{len(recs)} usable record(s)")
    if a.json:
        print(json.dumps(recs, indent=1, ensure_ascii=False)[:4000])
