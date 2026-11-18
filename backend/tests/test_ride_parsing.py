from datetime import datetime, timedelta, timezone

import pytest

from app.training.parsing import RideParseError, parse_ride
from tests.fit_writer import write_fit

START = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)

GARMIN_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Garmin Connect" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk>
    <name>Lunch Ride</name>
    <trkseg>
      <trkpt lat="45.5000" lon="-73.5500"><ele>20.4</ele><time>2026-06-01T13:00:00Z</time>
        <extensions><power>210</power><gpxtpx:TrackPointExtension><gpxtpx:hr>132</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
      </trkpt>
      <trkpt lat="45.5010" lon="-73.5510"><ele>21.0</ele><time>2026-06-01T13:00:05Z</time>
        <extensions><power>230</power><gpxtpx:TrackPointExtension><gpxtpx:hr>135</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
      </trkpt>
    </trkseg>
  </trk>
</gpx>"""


def test_reads_a_garmin_gpx_with_heart_rate_and_power() -> None:
    ride = parse_ride("ride.gpx", GARMIN_GPX)

    assert ride.name == "Lunch Ride"
    assert len(ride.samples) == 2
    first = ride.samples[0]
    assert (first.lat, first.lon, first.ele) == (45.5, -73.55, 20.4)
    assert (first.hr, first.power) == (132, 210)
    assert first.time == START
    assert ride.samples[1].time == START + timedelta(seconds=5)


def test_reads_gpx_without_extensions() -> None:
    data = GARMIN_GPX.replace(b"<extensions>", b"<!--").replace(b"</extensions>", b"-->")
    first = parse_ride("ride.gpx", data).samples[0]
    assert (first.hr, first.power) == (None, None)


def test_gpx_1_0_namespace_works() -> None:
    data = GARMIN_GPX.replace(b"http://www.topografix.com/GPX/1/1", b"http://www.topografix.com/GPX/1/0")
    assert len(parse_ride("ride.gpx", data).samples) == 2


def test_samples_are_put_in_time_order() -> None:
    data = GARMIN_GPX.replace(b"13:00:05Z", b"12:59:55Z")
    ride = parse_ride("ride.gpx", data)
    assert ride.samples[0].lat == 45.501


def test_a_planned_route_without_times_is_rejected() -> None:
    # Like the GPX this app exports: a track with no timestamps.
    route = b"""<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1"><trk><trkseg>
      <trkpt lat="45.5" lon="-73.55"/><trkpt lat="45.51" lon="-73.56"/></trkseg></trk></gpx>"""
    with pytest.raises(RideParseError, match="planned route"):
        parse_ride("route.gpx", route)


def test_malformed_xml_is_rejected() -> None:
    with pytest.raises(RideParseError, match="not valid GPX"):
        parse_ride("ride.gpx", b"<gpx><trk>")


def test_entity_expansion_is_refused() -> None:
    bomb = b"""<?xml version="1.0"?><!DOCTYPE gpx [<!ENTITY a "aaaaaaaaaa">
      <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><gpx>&b;</gpx>"""
    with pytest.raises(RideParseError):
        parse_ride("ride.gpx", bomb)


def test_other_files_are_rejected() -> None:
    with pytest.raises(RideParseError, match="not a GPX or FIT"):
        parse_ride("notes.txt", b"just some text")


def fit_points(count: int = 3) -> list[dict]:
    return [
        {
            "time": START + timedelta(seconds=i),
            "lat": 45.5 + i * 0.0001,
            "lon": -73.55,
            "ele": 20.0 + i,
            "hr": 130 + i,
            "power": 200 + i,
        }
        for i in range(count)
    ]


def test_reads_a_fit_file() -> None:
    ride = parse_ride("ride.fit", write_fit(fit_points()))

    assert ride.sport == "cycling"
    assert len(ride.samples) == 3
    last = ride.samples[-1]
    assert last.time == START + timedelta(seconds=2)
    assert last.lat == pytest.approx(45.5002, abs=1e-6)
    assert last.lon == pytest.approx(-73.55, abs=1e-6)
    assert last.ele == pytest.approx(22.0)
    assert (last.hr, last.power) == (132, 202)


def test_fit_is_recognised_by_content_not_name() -> None:
    assert len(parse_ride("download", write_fit(fit_points())).samples) == 3


def test_fit_without_heart_rate_or_power() -> None:
    points = [{**p, "hr": None, "power": None} for p in fit_points()]
    sample = parse_ride("ride.fit", write_fit(points)).samples[0]
    assert (sample.hr, sample.power) == (None, None)


def test_corrupt_fit_is_rejected() -> None:
    data = write_fit(fit_points())
    with pytest.raises(RideParseError, match="FIT"):
        parse_ride("ride.fit", data[:20] + b"\x00" * 10)
