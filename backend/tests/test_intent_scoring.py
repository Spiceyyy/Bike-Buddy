import pytest

from app.evaluation.intent_scoring import (
    distances_match,
    normalize_place,
    places_match,
    score_case,
    summarize,
)
from app.models.spec import RouteSpec, RouteType, Vibe

LOOP = RouteSpec(
    route_type=RouteType.LOOP,
    start_place="Old Port",
    target_distance_km=50,
    vibes=[Vibe.QUIET, Vibe.WATER],
)


def test_normalize_removes_accents_case_and_leading_the() -> None:
    assert normalize_place("The Vieux-Port de Montréal") == "vieux port de montreal"


@pytest.mark.parametrize(
    ("expected", "predicted", "match"),
    [
        ("Old Port", "the Old Port", True),
        ("Old Port", "Old Port of Montreal", True),
        ("Montréal-Nord", "montreal nord", True),
        ("Old Port", "Lachine", False),
        (None, None, True),
        ("Old Port", None, False),
        (None, "Old Port", False),
    ],
)
def test_places_match(expected: str | None, predicted: str | None, match: bool) -> None:
    assert places_match(expected, predicted) is match


@pytest.mark.parametrize(
    ("expected", "predicted", "match"),
    [(48.3, 48.28, True), (50, 52.4, True), (50, 53, False), (None, None, True), (50, None, False)],
)
def test_distances_match(expected: float | None, predicted: float | None, match: bool) -> None:
    assert distances_match(expected, predicted) is match


def test_identical_spec_is_exact_regardless_of_vibe_order() -> None:
    predicted = LOOP.model_copy(update={"vibes": [Vibe.WATER, Vibe.QUIET]})

    assert score_case("a", LOOP, predicted).exact


def test_one_wrong_field_is_not_exact_and_is_named() -> None:
    predicted = LOOP.model_copy(update={"vibes": [Vibe.QUIET]})

    score = score_case("a", LOOP, predicted)

    assert not score.exact
    assert score.decision_correct
    assert score.fields["vibes"] is False
    assert score.fields["start_place"] is True


def test_missing_tool_call_fails_every_field() -> None:
    score = score_case("a", LOOP, None)

    assert not score.decision_correct
    assert not any(score.fields.values())


def test_correctly_declining_to_plan_is_exact() -> None:
    score = score_case("a", None, None)

    assert score.exact and score.fields == {}


def test_planning_when_it_should_not_is_wrong() -> None:
    score = score_case("a", None, LOOP)

    assert not score.exact and not score.decision_correct


def test_searching_first_is_right_when_a_kind_of_place_was_asked_for() -> None:
    score = score_case("a", LOOP, None, expect_search=True, searched=True)

    assert score.exact and score.decision_correct and score.fields == {}


def test_planning_without_searching_is_wrong_when_search_was_needed() -> None:
    score = score_case("a", LOOP, LOOP, expect_search=True, searched=False)

    assert not score.decision_correct


def test_searching_when_not_needed_is_a_wrong_decision() -> None:
    score = score_case("a", LOOP, None, searched=True)

    assert not score.decision_correct


def test_via_places_must_match_in_order() -> None:
    expected = LOOP.model_copy(update={"via_places": ["Jacques-Cartier Bridge"]})
    right = LOOP.model_copy(update={"via_places": ["the Jacques-Cartier Bridge"]})

    assert score_case("a", expected, right).fields["via_places"] is True
    assert score_case("a", expected, LOOP).fields["via_places"] is False


def test_summary_excludes_non_ride_requests_from_field_accuracy() -> None:
    wrong_start = LOOP.model_copy(update={"start_place": "Lachine"})
    scores = [score_case("a", LOOP, LOOP), score_case("b", LOOP, wrong_start), score_case("c", None, None)]

    summary = summarize(scores)

    assert summary.total == 3
    assert summary.exact_match == pytest.approx(0.667)
    assert summary.decision_accuracy == 1.0
    assert summary.field_accuracy["start_place"] == 0.5
    assert summary.field_accuracy["route_type"] == 1.0
