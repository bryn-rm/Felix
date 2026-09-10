"""Quote provenance and partial batch validation without a database."""
import json

import pytest

from app.services.project_suggestion_service import validate_judgements


def test_bad_entries_do_not_discard_valid_suggestions():
    context = [{"id": "project", "text": "Orion delivery"}]
    candidates = [{"kind": "meeting", "source_id": str(i), "text": "Orion delivery"} for i in range(7)]
    good = {"candidate_id": "meeting:0", "score": 0.95, "explanation": "Same delivery.",
            "candidate_quote": "Orion delivery", "context_id": "project", "context_quote": "Orion delivery"}
    bad = [None, "invalid", {**good, "score": 0.1, "context_quote": "invented"},
           {**good, "candidate_id": "missing"}, {**good, "context_quote": "invented"},
           {**good, "score": "0.9"}, {**good, "extra": True}]
    results = validate_judgements(json.dumps({"suggestions": bad + [good, good] + [
        {**good, "candidate_id": f"meeting:{i}", "score": 0.9 + i / 100} for i in range(1, 7)
    ]}), context, candidates)
    assert len(results) == 5
    assert len({r["candidate_id"] for r in results}) == 5
    assert results[0]["score"] == 0.96


@pytest.mark.parametrize("raw", ['bad json', '{}', '{"suggestions": {}}', '{"suggestions": [], "extra": true}'])
def test_malformed_envelope_still_fails(raw):
    with pytest.raises(ValueError):
        validate_judgements(raw, [], [])
