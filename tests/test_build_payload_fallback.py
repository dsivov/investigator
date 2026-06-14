"""Unit tests for evaluate_investigator_server.build_payload's title-only fallback.

Body extraction via newspaper3k fails 20-30% of the time on real news runs
(paywall 403s, redirect loops, bot blocks). Without a fallback the article
is dropped entirely -- including the headline, which the LLM could still
mine for entity + relation signal.

This test pins the behaviour: failed-extraction articles must survive into
the payload with a synthesised title-only text and a body_available flag
set to False.

Standalone runner:
    PYTHONPATH=.:src /home/dsivov/.conda/envs/tangos/bin/python tests/test_build_payload_fallback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluate_investigator_server import build_payload  # noqa: E402


def _article(title="", text="", error=None, publisher="Reuters",
             real_url="https://reuters.com/foo", published_date="Mon, 01 Jun 2026 07:00:00 GMT"):
    return {
        "title": title,
        "publisher": publisher,
        "real_url": real_url,
        "published_date": published_date,
        "text": text,
        "error": error,
    }


def test_article_with_body_kept_as_before():
    a = _article(title="Iran supplies Russia drones", text="Full body about Iran supplying Russia with drones.")
    out = build_payload("Iran Russia drones", [a])
    body = out["Iran Russia drones"]
    assert len(body) == 1
    rec = next(iter(body.values()))
    assert rec["text"] == "Full body about Iran supplying Russia with drones."
    assert rec["body_available"] is True
    assert rec["title"] == "Iran supplies Russia drones"


def test_failed_extraction_article_kept_with_title_only_text():
    a = _article(title="Iran supplies Russia with fiber-optic FPV drones",
                 text="",
                 error="extract: ArticleException: 403 Forbidden")
    out = build_payload("Russia drones", [a])
    body = out["Russia drones"]
    assert len(body) == 1, "failed-extraction article must NOT be dropped"
    rec = next(iter(body.values()))
    assert rec["body_available"] is False
    # The synthesised text must include the headline so the LLM can extract from it.
    assert "Iran supplies Russia with fiber-optic FPV drones" in rec["text"]
    # The chunk must explicitly flag itself as headline-only so the LLM
    # treats it accordingly (extract only what is stated; do not hallucinate).
    assert "HEADLINE ONLY" in rec["text"]
    # Publisher + date carry through for the analyst.
    assert "Reuters" in rec["text"]
    # Title field still set.
    assert rec["title"] == "Iran supplies Russia with fiber-optic FPV drones"


def test_failed_extraction_with_no_title_is_still_dropped():
    a = _article(title="", text="", error="extract: failed")
    out = build_payload("Q", [a])
    # No title AND no body -> nothing to feed the LLM, skip entirely.
    assert out["Q"] == {}


def test_failed_extraction_error_message_surfaced_in_chunk():
    a = _article(title="Putin tests China's appetite for Russian gas",
                 text="",
                 error="extract: ArticleException: 403 Forbidden")
    out = build_payload("Q", [a])
    rec = next(iter(out["Q"].values()))
    assert "403" in rec["text"], "the failure reason should be visible in the chunk for debuggability"


def test_failed_extraction_without_error_field_uses_generic_reason():
    # error key not set at all
    a = {"title": "Some headline", "publisher": "FT", "real_url": "u",
         "published_date": "d", "text": ""}
    out = build_payload("Q", [a])
    rec = next(iter(out["Q"].values()))
    assert "body extraction failed" in rec["text"]
    assert rec["body_available"] is False


def test_mixed_batch_preserves_both_kinds():
    arts = [
        _article(title="Successful article", text="Full body of successful article."),
        _article(title="Paywalled headline", text="", error="extract: 403"),
        _article(title="Another good one", text="Another full body."),
    ]
    out = build_payload("Q", arts)
    body = out["Q"]
    assert len(body) == 3
    flags = [rec["body_available"] for rec in body.values()]
    assert flags.count(True) == 2
    assert flags.count(False) == 1


def _run_standalone() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as e:
            fails += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
