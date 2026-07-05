"""PROTOTYPE (throwaway) — claim verification via adversarial retrieval + stance.

De-risks the claim-driven mode discussed in design:
  1. PLAN   — an LLM turns a claim into SUPPORTING *and* REFUTING keyword search
              angles (the anti-confirmation-bias step).
  2. RETRIEVE — reuse the existing NL-capable sources (GDELT + Wikipedia).
  3. STANCE — an LLM classifies each snippet: SUPPORTS / REFUTES / NEUTRAL vs the claim.
  4. VERDICT — aggregate (confidence-weighted, distinct-source-aware) into an
               ICD-203 confidence verdict, with the supporting & refuting evidence.

NOT wired into the pipeline. Run:
  PYTHONPATH=.:src python research/prototype_claim_verify.py "your claim here"
"""
from __future__ import annotations

import json
import sys

from dotenv import load_dotenv

load_dotenv()
from openai import OpenAI  # noqa: E402

from research.search_sources import fetch_gdelt, fetch_wikipedia  # noqa: E402

client = OpenAI()
MODEL = "gpt-4o-mini"

# ICD-203 ladder by net (support − refute) signal in [-1, 1].
_LADDER = [
    (0.80, "Almost Certainly"), (0.55, "Highly Likely"), (0.25, "Likely"),
    (-0.25, "Roughly Even Chance"), (-0.55, "Unlikely"), (-0.80, "Highly Unlikely"),
    (-1.01, "Almost Certainly Not"),
]


def _llm_json(system: str, user: str) -> dict:
    r = client.chat.completions.create(
        model=MODEL, temperature=0.0,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    return json.loads(r.choices[0].message.content)


def plan_queries(claim: str) -> dict:
    system = ("You are an OSINT fact-checker planning web/news searches to TEST a claim. "
              "Avoid confirmation bias: produce queries that would surface SUPPORTING evidence "
              "AND queries that would surface REFUTING evidence (denials, rebuttals, corrections, "
              "'no evidence', debunks). Use short keyword phrases, not sentences.")
    user = (f'Claim: "{claim}"\n'
            'Return JSON: {"support": [2 keyword queries], "refute": [2 keyword queries]}')
    return _llm_json(system, user)


def classify_stance(claim: str, item: dict) -> dict:
    snippet = (f"{item.get('title','')} — {(item.get('text') or '')[:800]}").strip()
    system = ("You judge whether a snippet SUPPORTS, REFUTES, or is NEUTRAL toward a specific "
              "claim, based ONLY on the snippet's content. NEUTRAL if it is off-topic or merely "
              "mentions the entities without bearing on the claim.")
    user = (f'Claim: "{claim}"\nSnippet: {snippet}\n'
            'Return JSON: {"stance":"SUPPORTS|REFUTES|NEUTRAL","confidence":0.0-1.0,"reason":"one sentence"}')
    return _llm_json(system, user)


def verdict_label(net: float) -> str:
    for thresh, label in _LADDER:
        if net >= thresh:
            return label
    return "Almost Certainly Not"


def main(claim: str) -> int:
    print(f"\nCLAIM: {claim}\n" + "=" * 70)
    plan = plan_queries(claim)
    print(f"PLAN — support angles: {plan.get('support')}")
    print(f"PLAN — refute  angles: {plan.get('refute')}\n")

    # Retrieve
    items: list[dict] = []
    for q in (plan.get("support", []) + plan.get("refute", [])):
        try:
            items += fetch_gdelt(q, 3) + fetch_wikipedia(q, 2)
        except Exception as e:  # noqa: BLE001
            print(f"  [retrieve] '{q}' failed: {type(e).__name__}: {e}")
    # dedup by url|title
    seen, uniq = set(), []
    for it in items:
        k = it.get("url") or it.get("title")
        if k and k not in seen and (it.get("text") or "").strip():
            seen.add(k); uniq.append(it)
    print(f"RETRIEVED {len(uniq)} usable snippets across {len(plan.get('support',[]))+len(plan.get('refute',[]))} queries\n")

    # Stance
    sup_w = ref_w = 0.0
    sup_src, ref_src = set(), set()
    rows = []
    for it in uniq:
        try:
            s = classify_stance(claim, it)
        except Exception as e:  # noqa: BLE001
            continue
        stance, conf = s.get("stance", "NEUTRAL"), float(s.get("confidence") or 0)
        src = (it.get("url") or "").split("/")[2] if it.get("url") else it.get("source", "?")
        rows.append((stance, conf, src, it.get("title", "")[:70], s.get("reason", "")[:90]))
        if stance == "SUPPORTS":
            sup_w += conf; sup_src.add(src)
        elif stance == "REFUTES":
            ref_w += conf; ref_src.add(src)

    tot = sup_w + ref_w
    net = (sup_w - ref_w) / tot if tot else 0.0
    print("EVIDENCE (stance · conf · source · title):")
    for stance, conf, src, title, reason in sorted(rows, key=lambda r: (r[0], -r[1])):
        mark = {"SUPPORTS": "✅", "REFUTES": "❌", "NEUTRAL": "· "}.get(stance, "?")
        print(f"  {mark} {stance:8} {conf:.2f} [{src}] {title}")
    print("\n" + "=" * 70)
    print(f"VERDICT: **{verdict_label(net)}** the claim is supported.")
    print(f"  support: {sup_w:.2f} weight / {len(sup_src)} sources | "
          f"refute: {ref_w:.2f} weight / {len(ref_src)} sources | net={net:+.2f}")
    return 0


if __name__ == "__main__":
    claim = sys.argv[1] if len(sys.argv) > 1 else \
        "Huione Group laundered money for North Korean state-sponsored hackers."
    raise SystemExit(main(claim))
