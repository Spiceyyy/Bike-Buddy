"""Choosing distinct, labelled route options. Pure logic: no routing engine or database."""

from app.models.route import LineString, RouteFeature, RouteProperties
from app.models.scenic import RouteScenic, ScenicBand
from app.models.spec import Climbing, RouteSpec, RouteType
from app.scoring.candidates import (
    ALTERNATIVE,
    BEST_MATCH,
    FLATTEST,
    HILLIEST,
    MOST_SCENIC,
    choose_options,
)

SPEC = RouteSpec(route_type=RouteType.LOOP, start_place="Old Port", target_distance_km=10)


def route(offset: float, climb: float = 100, scenic: float = 0.4) -> RouteFeature:
    """A 2 km east-west line; `offset` (degrees north) moves it onto different roads."""
    return RouteFeature(
        geometry=LineString(coordinates=[(-73.60, 45.50 + offset), (-73.5746, 45.50 + offset)]),
        properties=RouteProperties(
            distance_m=10_000,
            duration_s=2_400,
            profile="scenic",
            ascend_m=climb,
            scenic=RouteScenic(
                score=scenic,
                matched_share=1,
                band_shares={ScenicBand.SCENIC: scenic},
                stretches=[],
            ),
        ),
    )


def test_best_route_comes_first_and_others_are_labelled_by_what_they_win() -> None:
    best = route(0.00)
    scenic = route(0.01, scenic=0.8)
    hilly = route(0.02, climb=400)
    flat = route(0.03, climb=20)

    options = choose_options([best, scenic, hilly, flat], SPEC)

    assert [(label, r) for label, r in options] == [
        (BEST_MATCH, best),
        (MOST_SCENIC, scenic),
        (HILLIEST, hilly),
    ]


def test_near_copies_are_not_offered_twice() -> None:
    best = route(0.0)
    copy = route(0.0001, scenic=0.9)  # about 11 m away: the same roads
    other = route(0.02)

    options = choose_options([best, copy, other], SPEC)

    assert [r for _, r in options] == [best, other]
    assert options[1][0] == ALTERNATIVE


def test_criterion_is_skipped_when_the_best_match_already_wins_it() -> None:
    best = route(0.0, scenic=0.9)

    options = choose_options([best, route(0.01, scenic=0.5)], SPEC)

    assert [label for label, _ in options] == [BEST_MATCH, ALTERNATIVE]


def test_rider_seeking_climbs_is_not_offered_the_flattest_route() -> None:
    spec = SPEC.model_copy(update={"climbing": Climbing.SEEK})
    best = route(0.0, climb=300)
    flat = route(0.01, climb=10)

    labels = [label for label, _ in choose_options([best, flat], spec)]

    assert FLATTEST not in labels


def test_at_most_three_options() -> None:
    routes = [route(i * 0.01, climb=100 + i) for i in range(6)]

    assert len(choose_options(routes, SPEC)) == 3
