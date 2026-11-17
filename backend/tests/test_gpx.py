import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from app.models.gpx import GpxRequest
from app.routing.gpx import GPX_NAMESPACE, build_gpx, gpx_filename

NS = {"g": GPX_NAMESPACE}
TRACK = [(-73.553, 45.504), (-73.5601234567, 45.5), (-73.57, 45.49)]


def make_request(**overrides) -> GpxRequest:
    body = {
        "name": "Loop from Vieux-Port",
        "track": {"type": "LineString", "coordinates": TRACK},
        "waypoints": [
            {"name": "Vieux-Port", "coordinate": {"lat": 45.504, "lon": -73.553}, "kind": "start"},
            {"name": "Café Olimpico", "coordinate": {"lat": 45.5, "lon": -73.56}, "kind": "stop"},
        ],
    }
    body.update(overrides)
    return GpxRequest.model_validate(body)


def parse(request: GpxRequest) -> ET.Element:
    return ET.fromstring(build_gpx(request))


def test_is_gpx_1_1() -> None:
    root = parse(make_request())
    assert root.tag == f"{{{GPX_NAMESPACE}}}gpx"
    assert root.get("version") == "1.1"
    assert root.get("creator") == "Scenic Route Finder"


def test_starts_with_an_xml_declaration() -> None:
    assert build_gpx(make_request()).startswith(b"<?xml version='1.0' encoding='utf-8'?>")


def test_track_follows_the_route_point_for_point() -> None:
    root = parse(make_request())
    points = root.findall("g:trk/g:trkseg/g:trkpt", NS)
    assert [(float(p.get("lon")), float(p.get("lat"))) for p in points] == [
        (-73.553, 45.504),
        (-73.560123, 45.5),
        (-73.57, 45.49),
    ]
    assert root.findtext("g:trk/g:type", namespaces=NS) == "cycling"


def test_coordinates_are_rounded_to_six_decimals() -> None:
    point = parse(make_request()).findall("g:trk/g:trkseg/g:trkpt", NS)[1]
    assert (point.get("lat"), point.get("lon")) == ("45.500000", "-73.560123")


def test_names_the_route() -> None:
    root = parse(make_request())
    assert root.findtext("g:metadata/g:name", namespaces=NS) == "Loop from Vieux-Port"
    assert root.findtext("g:trk/g:name", namespaces=NS) == "Loop from Vieux-Port"


def test_waypoints_come_before_the_track() -> None:
    # GPX 1.1 requires metadata, then wpt, then trk.
    root = parse(make_request())
    assert [child.tag.split("}")[1] for child in root] == ["metadata", "wpt", "wpt", "trk"]


def test_waypoints_keep_names_and_kinds() -> None:
    waypoints = parse(make_request()).findall("g:wpt", NS)
    assert [w.findtext("g:name", namespaces=NS) for w in waypoints] == ["Vieux-Port", "Café Olimpico"]
    assert [w.findtext("g:type", namespaces=NS) for w in waypoints] == ["start", "stop"]
    assert waypoints[1].get("lat") == "45.500000"


def test_special_characters_are_escaped() -> None:
    request = make_request(name='Rue "A" & <B>')
    assert parse(request).findtext("g:metadata/g:name", namespaces=NS) == 'Rue "A" & <B>'


def test_rejects_a_track_of_one_point() -> None:
    with pytest.raises(ValidationError):
        make_request(track={"type": "LineString", "coordinates": [(-73.5, 45.5)]})


def test_rejects_swapped_coordinates() -> None:
    with pytest.raises(ValidationError):
        make_request(track={"type": "LineString", "coordinates": [(45.5, -73.5), (45.6, -173.5)]})


def test_rejects_an_unknown_waypoint_kind() -> None:
    with pytest.raises(ValidationError):
        make_request(
            waypoints=[{"name": "X", "coordinate": {"lat": 45.5, "lon": -73.5}, "kind": "summit"}]
        )


@pytest.mark.parametrize(
    "name, filename",
    [
        ("Loop from Vieux-Port", "loop-from-vieux-port.gpx"),
        ("Loop from L'Île-des-Sœurs", "loop-from-l-ile-des-soeurs.gpx"),
        ("McGill → Parc Jarry", "mcgill-parc-jarry.gpx"),
        ("→ ✓", "route.gpx"),
    ],
)
def test_filename_is_plain_ascii(name: str, filename: str) -> None:
    assert gpx_filename(name) == filename
