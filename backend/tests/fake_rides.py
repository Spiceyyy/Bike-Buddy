"""An in-memory RideStore and sample ride files for trainer-mode tests."""

import math
from datetime import datetime, timedelta, timezone

from app.models.route import LineString
from app.models.training import LoadMethod, Ride, RideDetail, RideMetrics, RiderProfile

DEGREES_PER_M = 180 / (math.pi * 6_371_000.0)


class FakeRideStore:
    def __init__(self) -> None:
        self.rides: dict[int, RideDetail] = {}
        self.hashes: set[str] = set()
        self.saved_profile = RiderProfile()
        self.next_id = 1

    async def add(self, filename, sha256, name, metrics: RideMetrics, geometry: LineString) -> Ride | None:
        if sha256 in self.hashes:
            return None
        self.hashes.add(sha256)
        ride = RideDetail(id=self.next_id, filename=filename, name=name, geometry=geometry, **metrics.model_dump())
        self.rides[ride.id] = ride
        self.next_id += 1
        return Ride(**ride.model_dump(exclude={"geometry"}))

    async def list_rides(self) -> list[Ride]:
        rides = sorted(self.rides.values(), key=lambda r: r.started_at, reverse=True)
        return [Ride(**r.model_dump(exclude={"geometry"})) for r in rides]

    async def get(self, ride_id: int) -> RideDetail | None:
        return self.rides.get(ride_id)

    async def delete(self, ride_id: int) -> bool:
        return self.rides.pop(ride_id, None) is not None

    async def profile(self) -> RiderProfile:
        return self.saved_profile

    async def save_profile(self, profile: RiderProfile) -> None:
        self.saved_profile = profile

    async def update_loads(self, loads: list[tuple[int, float, LoadMethod, float | None]]) -> None:
        for ride_id, load, method, intensity in loads:
            ride = self.rides[ride_id]
            self.rides[ride_id] = ride.model_copy(update={"load": load, "load_method": method, "intensity": intensity})


def gpx_ride(
    start: datetime = datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc),
    seconds: int = 1800,
    speed_ms: float = 7.0,
    hr: int | None = 150,
    power: int | None = 200,
    name: str = "Morning Ride",
) -> bytes:
    """A GPX ride heading north, one point every 5 seconds."""
    points = []
    for i in range(0, seconds + 1, 5):
        extensions = ""
        if hr is not None or power is not None:
            extensions = "<extensions>"
            if power is not None:
                extensions += f"<power>{power}</power>"
            if hr is not None:
                extensions += f"<gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr></gpxtpx:TrackPointExtension>"
            extensions += "</extensions>"
        time = (start + timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        lat = 45.5 + i * speed_ms * DEGREES_PER_M
        points.append(f'<trkpt lat="{lat:.7f}" lon="-73.55"><ele>20</ele><time>{time}</time>{extensions}</trkpt>')
    return (
        '<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
        f"<trk><name>{name}</name><trkseg>{''.join(points)}</trkseg></trk></gpx>"
    ).encode()
