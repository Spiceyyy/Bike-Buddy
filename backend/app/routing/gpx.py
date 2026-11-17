"""GPX 1.1 export, so a planned route can be ridden on a bike computer or phone app.

The track is the routing engine's geometry, unchanged. Waypoints mark the
start, end, café stop and places passed. No timestamps or elevation are
written: devices and apps add elevation from their own data, and SRTM
figures run high in the city.
"""

import re
import unicodedata
import xml.etree.ElementTree as ET

from app.models.gpx import GpxRequest

GPX_NAMESPACE = "http://www.topografix.com/GPX/1/1"
CREATOR = "Scenic Route Finder"
# 6 decimal places is about 0.1 m, finer than the routing graph.
COORDINATE_FORMAT = "{:.6f}"
WAYPOINT_SYMBOLS = {"start": "Flag, Green", "end": "Flag, Red", "stop": "Restaurant", "via": "Scenic Area"}


def build_gpx(request: GpxRequest) -> bytes:
    """The route as a GPX 1.1 document, UTF-8 encoded."""
    ET.register_namespace("", GPX_NAMESPACE)
    gpx = ET.Element(_tag("gpx"), {"version": "1.1", "creator": CREATOR})

    metadata = ET.SubElement(gpx, _tag("metadata"))
    _text(metadata, "name", request.name)

    for waypoint in request.waypoints:
        wpt = ET.SubElement(gpx, _tag("wpt"), _position(waypoint.coordinate.lon, waypoint.coordinate.lat))
        _text(wpt, "name", waypoint.name)
        _text(wpt, "sym", WAYPOINT_SYMBOLS[waypoint.kind])
        _text(wpt, "type", waypoint.kind)

    track = ET.SubElement(gpx, _tag("trk"))
    _text(track, "name", request.name)
    _text(track, "type", "cycling")
    segment = ET.SubElement(track, _tag("trkseg"))
    for lon, lat in request.track.coordinates:
        ET.SubElement(segment, _tag("trkpt"), _position(lon, lat))

    ET.indent(gpx)
    return ET.tostring(gpx, encoding="utf-8", xml_declaration=True)


def gpx_filename(name: str) -> str:
    """An ASCII file name from the route name: "Loop from Île-des-Sœurs" -> "loop-from-ile-des-soeurs.gpx"."""
    ascii_name = unicodedata.normalize("NFKD", name.replace("œ", "oe").replace("Œ", "Oe"))
    ascii_name = ascii_name.encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:60].strip("-")
    return f"{slug or 'route'}.gpx"


def _tag(name: str) -> str:
    return f"{{{GPX_NAMESPACE}}}{name}"


def _text(parent: ET.Element, name: str, value: str) -> None:
    ET.SubElement(parent, _tag(name)).text = value


def _position(lon: float, lat: float) -> dict[str, str]:
    return {"lat": COORDINATE_FORMAT.format(lat), "lon": COORDINATE_FORMAT.format(lon)}
