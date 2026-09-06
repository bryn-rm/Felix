"""
Offline live-assist quality benchmark — run explicitly with:

    cd backend && python -m pytest -m assist_eval -s

Requires a real key for AI_MODEL_FAST's provider (skips on test stubs).
Costs real model calls (one per gate-passed evaluation — roughly 15-25 calls
per full run).

This measures what the functional tests can't: whether the suggestions are
USEFUL. It replays the scenarios in evals/assist_benchmark.py through the same
deterministic CandidateGate + watch prompt + acceptance gate the watcher uses
(timing simulated from segment timestamps, DB/WS bypassed) and scores:

  • intervention precision (primary — target ≥ 0.85): of the cards shown, how
    many landed on an annotated should-show moment with an acceptable kind;
  • opportunity recall (0.4–0.6 is fine for v1): of the annotated moments, how
    many produced a card;
  • groundedness: body claims (numbers, names) traceable to digest/transcript;
  • redundancy: duplicate cards the dedupe gate had to suppress.

Tune SCORE_THRESHOLD / gate lists / prompt wording against this harness and
bump PROMPT_VERSIONS["live_assist_watch"] when the prompt changes.
"""

import json
import re

import pytest

from app.config import settings
from app.prompts.live_assist import (
    LIVE_ASSIST_SYSTEM,
    LIVE_ASSIST_WATCH_PROMPT,
    format_shown_titles,
)
from app.services import live_assist_service as las
from app.services.live_assist_service import CandidateGate, _normalize_title
from evals.assist_benchmark import SCENARIOS

pytestmark = pytest.mark.assist_eval

# Cards attribute to an annotated moment within this many finals back.
ATTRIBUTION_WINDOW = 2


def _real_key_available() -> bool:
    key = (settings.OPENAI_API_KEY if settings.AI_MODEL_FAST.startswith("gpt-")
           else settings.ANTHROPIC_API_KEY)
    return bool(key and not key.startswith("test-"))


async def _watch_once(digest: dict, shown_titles: list[str],
                      window: list[tuple[str, str]]) -> dict | None:
    prompt = LIVE_ASSIST_WATCH_PROMPT.format(
        meeting_title="(benchmark)",
        template="general",
        context_digest=las.format_digest(digest),
        shown_titles=format_shown_titles(shown_titles),
        transcript_window="\n".join(f"{s}: {t}" for s, t in window) or "(no transcript yet)",
    )
    response = await las._ai.call_fast(
        feature="live_assist_watch",
        model=settings.AI_MODEL_FAST,
        max_tokens=350,
        timeout=las.CALL_TIMEOUT_S,
        system=LIVE_ASSIST_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.error:
        return None
    try:
        result = json.loads(las._ai._strip_markdown_fences(response.text))
    except json.JSONDecodeError:
        return None
    card = result.get("card") if isinstance(result, dict) else None
    return card if isinstance(card, dict) else None


def _grounded(body: str, source_text: str) -> bool:
    """Every number and capitalized name in the body must appear in the digest
    or transcript. Heuristic — flags invented figures/entities, not paraphrase."""
    source = source_text.lower()
    for num in re.findall(r"\d[\d,]*", body):
        if num.replace(",", "") not in source.replace(",", ""):
            return False
    for name in re.findall(r"\b[A-Z][a-z]{2,}\b", body):
        lowered = name.lower()
        if lowered in {"the", "she", "her", "his", "they", "their"}:
            continue
        if lowered not in source:
            return False
    return True


async def test_assist_benchmark():
    if not _real_key_available():
        pytest.skip(f"assist_eval needs a real provider key for {settings.AI_MODEL_FAST}")

    shown_total = 0
    true_positives = 0
    false_positives: list[tuple[str, str]] = []
    moments_total = 0
    moments_hit = 0
    grounded_count = 0
    redundancy_suppressed = 0
    watch_calls = 0

    for scenario in SCENARIOS:
        gate = CandidateGate(scenario["keywords"])
        shown_titles: list[str] = []
        shown_normalized: set[str] = set()
        window: list[tuple[str, str]] = []
        finals = scenario["finals"]
        moments_total += sum(1 for seg in finals if seg["expect"])
        satisfied: set[int] = set()
        last_call_ts = float("-inf")

        for i, seg in enumerate(finals):
            window.append((seg["speaker"], seg["text"]))
            trigger = gate.observe(seg["speaker"], seg["text"])
            if trigger is None and not gate.accumulation_ready(seg["ts"] - last_call_ts):
                continue
            last_call_ts = seg["ts"]
            gate.reset_accumulation()
            watch_calls += 1

            card = await _watch_once(scenario["digest"], shown_titles, window)
            if not card:
                continue
            kind = str(card.get("kind") or "")
            title = str(card.get("title") or "")
            body = str(card.get("body") or "")
            score = float(card.get("usefulness_score") or 0.0)
            if not title or not body or score < las.SCORE_THRESHOLD:
                continue
            if _normalize_title(title) in shown_normalized:
                redundancy_suppressed += 1
                continue

            # Card shown — attribute it to a nearby annotated moment.
            shown_total += 1
            shown_titles.append(title)
            shown_normalized.add(_normalize_title(title))
            source_text = las.format_digest(scenario["digest"]) + " " + " ".join(
                t for _, t in window
            )
            if _grounded(body, source_text):
                grounded_count += 1

            attributed = False
            for j in range(i, max(-1, i - ATTRIBUTION_WINDOW - 1), -1):
                expect = finals[j]["expect"]
                if expect and j not in satisfied and kind in expect:
                    satisfied.add(j)
                    moments_hit += 1
                    true_positives += 1
                    attributed = True
                    break
            if not attributed:
                false_positives.append((scenario["name"], f"{kind}: {title}"))

    precision = true_positives / shown_total if shown_total else 1.0
    recall = moments_hit / moments_total if moments_total else 1.0
    groundedness = grounded_count / shown_total if shown_total else 1.0

    print("\n=== live-assist benchmark ===")
    print(f"watch calls:            {watch_calls}")
    print(f"cards shown:            {shown_total}")
    print(f"intervention precision: {precision:.2f}  (target ≥ 0.85)")
    print(f"opportunity recall:     {recall:.2f}  (0.4–0.6 acceptable)")
    print(f"groundedness:           {groundedness:.2f}")
    print(f"redundancy suppressed:  {redundancy_suppressed}")
    for name, desc in false_positives:
        print(f"  FP [{name}] {desc}")

    # Guardrails — precision is the product-defining metric ("silence is
    # success"); recall only needs to clear a low floor in v1.
    assert precision >= 0.85, f"precision {precision:.2f} below 0.85 — see FP list above"
    assert recall >= 0.3, f"recall {recall:.2f} below the 0.3 floor"
    assert groundedness >= 0.8, f"groundedness {groundedness:.2f} below 0.8"
