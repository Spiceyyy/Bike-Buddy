"""Read recorded rides from GPX and FIT files into one list of samples.

Garmin, Wahoo, Strava and most apps export one of the two. GPX keeps heart
rate and power in extensions (Garmin's TrackPointExtension and others), so
they are matched by element name, whatever the namespace. Files come from
riders, so XML is parsed with defusedxml.
"""

import io
import struct
from dataclasses import dataclass
from datetime import datetime, timezone

import fitdecode
from defusedxml import ElementTree as SafeET

SEMICIRCLES_TO_DEGREES = 180 / 2**31
MAX_SAMPLES = 200_000


class RideParseError(Exception):
    """The file is not a recorded ride this app can read. The message is shown to the rider."""


@dataclass(frozen=True)
class Sample:
    time: datetime
    lat: float
    lon: float
    ele: float | None = None
    hr: int | None = None
    power: int | None = None


@dataclass(frozen=True)
class ParsedRide:
    samples: list[Sample]
    name: str | None = None
    sport: str | None = None


def parse_ride(filename: str, data: bytes) -> ParsedRide:
    """Parse a .gpx or .fit file, chosen by its content, falling back on its name."""
    if data[8:12] == b".FIT":
        ride = _parse_fit(data)
    elif data.lstrip()[:1] == b"<" or filename.lower().endswith(".gpx"):
        ride = _parse_gpx(data)
    else:
        raise RideParseError("is not a GPX or FIT file")
    return _checked(ride)


def _checked(ride: ParsedRide) -> ParsedRide:
    if not ride.samples:
        raise RideParseError("has no timed track points: it may be a planned route, not a recorded ride")
    if len(ride.samples) > MAX_SAMPLES:
        raise RideParseError(f"has more than {MAX_SAMPLES} points")
    samples = sorted(ride.samples, key=lambda s: s.time)
    if len(samples) < 2 or samples[-1].time == samples[0].time:
        raise RideParseError("is too short to be a ride")
    return ParsedRide(samples=samples, name=ride.name, sport=ride.sport)


# --- GPX ---------------------------------------------------------------------


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_gpx(data: bytes) -> ParsedRide:
    try:
        root = SafeET.fromstring(data)
    except Exception as exc:  # defusedxml raises several types for bad or hostile XML
        raise RideParseError("is not valid GPX") from exc
    if _local(root.tag) != "gpx":
        raise RideParseError("is not valid GPX")

    name = None
    samples = []
    for element in root.iter():
        tag = _local(element.tag)
        if tag == "trk" and name is None:
            name = next((c.text for c in element if _local(c.tag) == "name" and c.text), None)
        if tag != "trkpt":
            continue
        time = ele = hr = power = None
        for child in element.iter():
            child_tag = _local(child.tag).lower()
            text = (child.text or "").strip()
            if not text:
                continue
            if child_tag == "time":
                time = _parse_time(text)
            elif child_tag == "ele":
                ele = _number(text)
            elif child_tag == "hr":
                hr = _whole(text)
            elif child_tag in ("power", "powerinwatts", "watts"):
                power = _whole(text)
        try:
            lat, lon = float(element.get("lat")), float(element.get("lon"))
        except (TypeError, ValueError):
            continue
        if time is not None:
            samples.append(Sample(time=time, lat=lat, lon=lon, ele=ele, hr=hr, power=power))
    return ParsedRide(samples=samples, name=name, sport="cycling")


def _parse_time(text: str) -> datetime | None:
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _whole(text: str) -> int | None:
    value = _number(text)
    return round(value) if value is not None else None


# --- FIT ---------------------------------------------------------------------


def _parse_fit(data: bytes) -> ParsedRide:
    samples = []
    sport = None
    try:
        with fitdecode.FitReader(io.BytesIO(data), check_crc=fitdecode.CrcCheck.WARN) as reader:
            for frame in reader:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name == "sport" and sport is None:
                    sport = _field(frame, "sport")
                elif frame.name == "record":
                    sample = _fit_sample(frame)
                    if sample is not None:
                        samples.append(sample)
    # fitdecode raises plain errors for some truncated or corrupt files.
    except (fitdecode.FitError, ValueError, KeyError, IndexError, EOFError, struct.error) as exc:
        raise RideParseError("is not a valid FIT file") from exc
    return ParsedRide(samples=samples, sport=str(sport) if sport is not None else None)


def _field(frame: "fitdecode.FitDataMessage", name: str):
    return frame.get_value(name, fallback=None)


def _fit_sample(frame: "fitdecode.FitDataMessage") -> Sample | None:
    time = _field(frame, "timestamp")
    lat = _field(frame, "position_lat")
    lon = _field(frame, "position_long")
    if time is None or lat is None or lon is None:
        return None
    if time.tzinfo is None:
        time = time.replace(tzinfo=timezone.utc)
    ele = _field(frame, "enhanced_altitude")
    if ele is None:
        ele = _field(frame, "altitude")
    hr = _field(frame, "heart_rate")
    power = _field(frame, "power")
    return Sample(
        time=time,
        lat=lat * SEMICIRCLES_TO_DEGREES,
        lon=lon * SEMICIRCLES_TO_DEGREES,
        ele=float(ele) if ele is not None else None,
        hr=int(hr) if hr is not None else None,
        power=int(power) if power is not None else None,
    )
