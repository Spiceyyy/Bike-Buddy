"""Scores a parsed RouteSpec against a hand-labelled one. Contains no LLM calls."""

import re
import unicodedata

from pydantic import BaseModel

from app.models.spec import RouteSpec

DISTANCE_TOLERANCE = 0.05
FIELD_NAMES = (
    "route_type",
    "start_place",
    "end_place",
    "target_distance_km",
    "surface",
    "max_climb_m",
    "climbing",
    "stop",
    "vibes",
)


class CaseScore(BaseModel):
    id: str
    decision_correct: bool  # Did the model call the tool exactly when it should?
    fields: dict[str, bool]  # Empty for requests that should not call the tool.
    exact: bool


class Summary(BaseModel):
    total: int
    exact_match: float
    decision_accuracy: float
    field_accuracy: dict[str, float]


def normalize_place(name: str) -> str:
    """Lowercase, remove accents and punctuation, and drop a leading "the"."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    words = re.sub(r"[^a-z0-9]+", " ", without_accents.casefold()).split()
    return " ".join(words[1:] if words[:1] == ["the"] else words)


def places_match(expected: str | None, predicted: str | None) -> bool:
    """Accept either name containing the other: "Old Port" and "Old Port of Montreal"."""
    if expected is None or predicted is None:
        return expected is predicted
    left, right = normalize_place(expected), normalize_place(predicted)
    return bool(left and right) and (left in right or right in left)


def distances_match(expected: float | None, predicted: float | None) -> bool:
    if expected is None or predicted is None:
        return expected is predicted
    return abs(predicted - expected) <= expected * DISTANCE_TOLERANCE


def score_case(case_id: str, expected: RouteSpec | None, predicted: RouteSpec | None) -> CaseScore:
    if expected is None:
        correct = predicted is None
        return CaseScore(id=case_id, decision_correct=correct, fields={}, exact=correct)

    if predicted is None:
        fields = dict.fromkeys(FIELD_NAMES, False)
        return CaseScore(id=case_id, decision_correct=False, fields=fields, exact=False)

    fields = {
        "route_type": expected.route_type == predicted.route_type,
        "start_place": places_match(expected.start_place, predicted.start_place),
        "end_place": places_match(expected.end_place, predicted.end_place),
        "target_distance_km": distances_match(
            expected.target_distance_km, predicted.target_distance_km
        ),
        "surface": expected.surface == predicted.surface,
        "max_climb_m": expected.max_climb_m == predicted.max_climb_m,
        "climbing": expected.climbing == predicted.climbing,
        "stop": expected.stop == predicted.stop,
        "vibes": set(expected.vibes) == set(predicted.vibes),
    }
    return CaseScore(id=case_id, decision_correct=True, fields=fields, exact=all(fields.values()))


def summarize(scores: list[CaseScore]) -> Summary:
    total = len(scores)
    spec_scores = [score for score in scores if score.fields]
    return Summary(
        total=total,
        exact_match=_rate(sum(score.exact for score in scores), total),
        decision_accuracy=_rate(sum(score.decision_correct for score in scores), total),
        field_accuracy={
            name: _rate(sum(score.fields[name] for score in spec_scores), len(spec_scores))
            for name in FIELD_NAMES
        },
    )


def _rate(count: int, total: int) -> float:
    return round(count / total, 3) if total else 0.0
