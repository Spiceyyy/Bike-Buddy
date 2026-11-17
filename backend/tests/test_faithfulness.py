from typing import Any

import pytest

from app.evaluation.faithfulness import (
    ProblemKind,
    candidate_names,
    check_reply,
    durations_in,
    feedback,
)
from app.models.llm import ToolResultMessage

MESSAGE = "A 30 km loop from the Old Port with a coffee stop"


def plan(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "ok",
        "route_type": "loop",
        "start": "Old Port",
        "end": "Old Port",
        "distance_km": 29.8,
        "riding_time": "1 h 27 min",
        "not_applied": [],
        "requested_distance_km": 30,
        "distance_on_target": True,
        "climb_m": 118,
        "stop": {"name": "Café Olimpico", "at_km": 14.2},
        "scenery": {
            "scenic_share": 0.42,
            "highlights": [
                {"name": "Canal de Lachine", "why": "along the water", "from_km": 2.1, "to_km": 9.4}
            ],
        },
        "other_options": [{"label": "Most scenic", "distance_km": 31.5}],
    }
    result.update(overrides)
    return result


def results(content: dict[str, Any], name: str = "plan_route") -> list[ToolResultMessage]:
    return [ToolResultMessage(call_id="1", name=name, content=content)]


def kinds(reply: str, content: dict[str, Any] | None = None, message: str = MESSAGE) -> list[str]:
    return [p.kind.value for p in check_reply(reply, message, results(content or plan()))]


GOOD_REPLY = (
    "Here is a 29.8 km loop from Old Port, about 1 h 27 min of riding. "
    "It follows the Canal de Lachine for water views, with a stop at Café Olimpico at km 14.2. "
    "You can pick another route or café on the map."
)


def test_faithful_reply_passes() -> None:
    assert kinds(GOOD_REPLY) == []


def test_french_reply_passes() -> None:
    reply = (
        "Voici une boucle de 29,8 km depuis Old Port, environ 1 h 27 min. "
        "Elle longe le Canal de Lachine, avec un arrêt au Café Olimpico au km 14,2."
    )
    assert kinds(reply, message="Une boucle de 30 km depuis le Vieux-Port avec un café") == []


@pytest.mark.parametrize(
    "text, minutes",
    [
        ("1 h 27 min", [87]),
        ("1 hour and 37 minutes", [97]),
        ("1h27", [87]),
        ("about 45 min", [45]),
        ("2 heures", [120]),
        ("1 heure et 5 minutes", [65]),
    ],
)
def test_reads_durations(text: str, minutes: list[int]) -> None:
    assert durations_in(text) == minutes


def test_flags_a_misread_riding_time() -> None:
    # The bug that started this check: 87 minutes became "1 hour and 37 minutes".
    reply = GOOD_REPLY.replace("1 h 27 min", "1 hour and 37 minutes")
    assert kinds(reply) == ["wrong_number"]


def test_allows_a_rounded_distance() -> None:
    assert kinds(GOOD_REPLY.replace("29.8 km", "30 km")) == []


def test_flags_an_invented_distance() -> None:
    assert kinds(GOOD_REPLY.replace("29.8 km", "35 km")) == ["wrong_number"]


def test_distances_from_other_options_and_highlights_are_allowed() -> None:
    reply = GOOD_REPLY + " The Most scenic option is 31.5 km; the canal runs from km 2.1 to 9.4 km."
    assert kinds(reply) == []


def test_checks_climbing_against_the_result() -> None:
    assert kinds(GOOD_REPLY + " Expect about 120 m of climbing.") == []
    assert kinds(GOOD_REPLY + " Expect about 300 m of climbing.") == ["wrong_number"]


def test_climbing_limit_from_the_message_is_allowed() -> None:
    message = "30 km loop from the Old Port, under 200 m of climbing"
    assert kinds(GOOD_REPLY + " That keeps you under 200 m.", message=message) == []


def test_checks_the_scenic_share() -> None:
    assert kinds(GOOD_REPLY + " 42% of it is scenic.") == []
    assert kinds(GOOD_REPLY + " 80% of it is scenic.") == ["wrong_number"]


def test_flags_a_missing_distance_and_time() -> None:
    assert kinds("Your loop follows the Canal de Lachine.") == ["missing_fact", "missing_fact"]


def test_flags_a_place_not_on_the_route() -> None:
    reply = GOOD_REPLY + " It also passes Mount Royal."
    problems = check_reply(reply, MESSAGE, results(plan()))
    assert [p.kind for p in problems] == [ProblemKind.UNKNOWN_NAME]
    assert "Mount Royal" in problems[0].detail


def test_flags_a_translated_name() -> None:
    # The result says "Canal de Lachine"; the prompt asks for names exactly as given.
    reply = GOOD_REPLY.replace("the Canal de Lachine", "the Lachine Canal")
    assert kinds(reply) == ["unknown_name"]


def test_names_from_the_message_are_allowed() -> None:
    message = "30 km loop from the Old Port past the Jacques-Cartier Bridge"
    assert kinds(GOOD_REPLY + " You asked to pass the Jacques-Cartier Bridge.", message=message) == []


def test_search_hits_that_were_not_routed_are_not_allowed() -> None:
    tool_results = [
        ToolResultMessage(
            call_id="1",
            name="search_places",
            content={"status": "ok", "places": [{"name": "Belvédère Kondiaronk"}]},
        ),
        *results(plan()),
    ]
    reply = GOOD_REPLY + " It passes Belvédère Kondiaronk."
    assert [p.kind for p in check_reply(reply, MESSAGE, tool_results)] == [ProblemKind.UNKNOWN_NAME]


def test_via_places_are_allowed() -> None:
    content = plan(via=["Belvédère Kondiaronk"])
    assert kinds(GOOD_REPLY + " It passes Belvédère Kondiaronk.", content) == []


@pytest.mark.parametrize(
    "text, names",
    [
        ("Ride along the Canal de Lachine to Parc Jean-Drapeau.", ["Canal de Lachine", "Parc Jean-Drapeau"]),
        ("The loop starts at Old Port.", ["Old Port"]),
        ("Your ride is 30 km. Enjoy!", []),
        ("You can pick another route or café on the map.", []),
        ("Elle passe par l'Oratoire Saint-Joseph.", ["l'Oratoire Saint-Joseph"]),
        # Found in faithfulness eval v1, where both were false alarms:
        ("You'll stop at the café.", []),
        (
            "It takes you on Rue Willibrord and the Piste Cyclable du Canal de Lachine.",
            ["Rue Willibrord", "Piste Cyclable du Canal de Lachine"],
        ),
    ],
)
def test_finds_candidate_names(text: str, names: list[str]) -> None:
    assert candidate_names(text) == names


def test_flags_an_unsupported_scenery_claim() -> None:
    # Faithfulness eval v2: the only highlight was "through green space".
    content = plan(
        scenery={
            "scenic_share": 0.1,
            "highlights": [
                {"name": "Rue Sainte-Catherine Ouest", "why": "through green space", "from_km": 1, "to_km": 2}
            ],
        }
    )
    reply = "A 29.8 km loop, about 1 h 27 min. It stops at Café Olimpico and follows the water."
    assert kinds(reply, content) == ["unsupported_scenery"]


def test_scenery_claims_backed_by_a_highlight_pass() -> None:
    # GOOD_REPLY says "for water views"; this says it plainly.
    assert kinds(GOOD_REPLY + " Much of it runs along the water.") == []
    assert kinds(GOOD_REPLY + " Elle longe le canal au bord de l'eau.") == []


def test_unsupported_preferences_are_not_scenery_claims() -> None:
    reply = GOOD_REPLY + " Quiet streets and green space preferences are not supported yet."
    assert kinds(reply) == []


def test_flags_each_unbacked_kind_of_scenery() -> None:
    reply = GOOD_REPLY + " It runs through green space on quiet streets."
    assert kinds(reply) == ["unsupported_scenery", "unsupported_scenery"]


def test_flags_a_surface_claim() -> None:
    # Faithfulness eval v3: the app has no surface data.
    reply = GOOD_REPLY + " Unpaved surfaces are not supported yet, so this route is paved."
    assert kinds(reply) == ["unsupported_scenery"]
    assert kinds(GOOD_REPLY + " Surface preferences are not supported yet.") == []


def test_flags_a_distance_caveat_on_a_loop_that_met_its_distance() -> None:
    reply = GOOD_REPLY + " This was the closest loop I could find to your requested distance."
    assert kinds(reply) == ["wrong_caveat"]


def test_a_french_note_is_not_a_name() -> None:
    assert candidate_names("Notez que la préférence n'est pas prise en charge.") == []


def test_flags_a_missing_stop_caveat() -> None:
    content = plan(stop_found=False)
    del content["stop"]
    reply = "Here is a 29.8 km loop from Old Port, about 1 h 27 min."
    assert kinds(reply, content) == ["missing_caveat"]
    assert kinds(reply + " No café was found near the route.", content) == []
    assert kinds(reply + " Aucun café n'a été trouvé près du trajet.", content) == []


def test_flags_a_missing_climbing_caveat() -> None:
    content = plan(climb_on_target=False, climb_m=230)
    message = "30 km loop from the Old Port, under 200 m of climbing"
    reply = GOOD_REPLY
    assert kinds(reply, content, message) == ["missing_caveat"]
    fixed = reply + " It has about 230 m of climbing, so the 200 m limit could not be met."
    assert kinds(fixed, content, message) == []


def test_flags_a_missing_distance_caveat() -> None:
    content = plan(distance_km=24.0, distance_on_target=False)
    reply = GOOD_REPLY.replace("29.8 km", "24 km")
    assert kinds(reply, content) == ["missing_caveat"]
    assert kinds(reply + " This was the closest loop to 30 km I could find.", content) == []


def test_replies_without_a_planned_route_pass() -> None:
    assert check_reply("I plan bike routes. What ride would you like?", "Hello", []) == []
    needs = results({"status": "needs_input", "missing": ["start"]})
    assert check_reply("Where would you like to start?", "30 km loop", needs) == []


def test_only_the_last_plan_counts() -> None:
    tool_results = [
        *results({"status": "invalid_arguments", "errors": []}),
        *results(plan()),
    ]
    assert check_reply(GOOD_REPLY, MESSAGE, tool_results) == []


def test_feedback_lists_each_problem() -> None:
    problems = check_reply("Your loop passes Mount Royal.", MESSAGE, results(plan()))
    text = feedback(problems)
    assert '"Mount Royal"' in text
    assert "the distance is not given" in text


WEATHER = {
    "temperature_c": "12 to 15",
    "rain_chance_pct": 70,
    "wind_kmh": 25,
    "wind_from": "west",
    "notes": [
        "rain likely (70% chance)",
        "windy: 25 km/h from the west, gusts to 50 km/h, into the wind for 40% of the route",
    ],
    "headwind_pct": 40,
}


def test_weather_notes_passed_on_pass() -> None:
    reply = GOOD_REPLY + " Rain is likely (70% chance), with 25 km/h wind from the west."
    assert kinds(reply, plan(weather=WEATHER)) == []


def test_a_wind_speed_is_not_a_distance() -> None:
    reply = GOOD_REPLY + " Expect gusts to 50 km/h and rain (70% chance)."
    assert kinds(reply, plan(weather=WEATHER)) == []


def test_flags_a_wrong_wind_speed_or_rain_chance() -> None:
    assert kinds(GOOD_REPLY + " Wind 40 km/h, rain likely.", plan(weather=WEATHER)) == ["wrong_number"]
    assert kinds(GOOD_REPLY + " Rain: 90% chance.", plan(weather=WEATHER)) == ["wrong_number"]


def test_checks_temperatures() -> None:
    assert kinds(GOOD_REPLY + " It will be 12 to 15 °C, rain likely.", plan(weather=WEATHER)) == []
    assert kinds(GOOD_REPLY + " It will be 25 °C, rain likely.", plan(weather=WEATHER)) == [
        "wrong_number"
    ]


def test_negative_temperatures_match() -> None:
    cold = {**WEATHER, "temperature_c": "-2 to 1", "notes": ["cold: -2 to 1 °C"]}
    assert kinds(GOOD_REPLY + " It will be cold, -2 to 1 °C.", plan(weather=cold)) == []


def test_flags_weather_notes_left_out() -> None:
    assert kinds(GOOD_REPLY, plan(weather=WEATHER)) == ["missing_fact"]


def test_fine_weather_need_not_be_mentioned() -> None:
    assert kinds(GOOD_REPLY, plan(weather={**WEATHER, "notes": []})) == []


def test_french_weather_note_passes() -> None:
    reply = (
        "Voici une boucle de 29,8 km depuis Old Port, environ 1 h 27 min. "
        "Pluie probable (70 %) et vent de 25 km/h."
    )
    message = "Une boucle de 30 km depuis le Vieux-Port"
    assert kinds(reply, plan(weather=WEATHER), message) == []
