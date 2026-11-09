import httpx
import pytest

from app.routing.geocoder import GeocodingError, NominatimGeocoder
from tests.fakes import BOUNDS

RESULT = {"name": "Vieux-Port", "display_name": "Vieux-Port, Montréal", "lat": "45.5", "lon": "-73.55"}


def make_geocoder(handler) -> NominatimGeocoder:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://geo")
    return NominatimGeocoder(http, min_seconds_between_requests=0)


async def test_restricts_search_to_map_region() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[RESULT])

    await make_geocoder(capture).search("Old Port", BOUNDS)

    params = seen[0].url.params
    assert params["q"] == "Old Port"
    assert params["bounded"] == "1"
    assert params["viewbox"] == "-74.0,45.7,-73.3,45.3"


async def test_drops_leading_the_before_searching() -> None:
    seen: list[httpx.Request] = []

    def capture(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[RESULT])

    await make_geocoder(capture).search("the Old Port", BOUNDS)

    assert seen[0].url.params["q"] == "Old Port"


async def test_returns_place_name_and_coordinate() -> None:
    place = await make_geocoder(lambda request: httpx.Response(200, json=[RESULT])).search(
        "Old Port", BOUNDS
    )

    assert place.name == "Vieux-Port"
    assert (place.coordinate.lat, place.coordinate.lon) == (45.5, -73.55)


async def test_falls_back_to_display_name_when_name_is_empty() -> None:
    result = {**RESULT, "name": ""}

    place = await make_geocoder(lambda request: httpx.Response(200, json=[result])).search(
        "Old Port", BOUNDS
    )

    assert place.name == "Vieux-Port"


async def test_no_match_returns_none() -> None:
    geocoder = make_geocoder(lambda request: httpx.Response(200, json=[]))

    assert await geocoder.search("Atlantis", BOUNDS) is None


async def test_repeat_queries_use_the_cache() -> None:
    calls: list[int] = []

    def count(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=[RESULT])

    geocoder = make_geocoder(count)
    await geocoder.search("Old Port", BOUNDS)
    await geocoder.search(" old port ", BOUNDS)

    assert len(calls) == 1


@pytest.mark.parametrize(
    "response",
    [httpx.Response(503), httpx.Response(200, json=[{"lat": "not-a-number"}])],
)
async def test_bad_responses_raise(response: httpx.Response) -> None:
    geocoder = make_geocoder(lambda request: response)

    with pytest.raises(GeocodingError):
        await geocoder.search("Old Port", BOUNDS)
