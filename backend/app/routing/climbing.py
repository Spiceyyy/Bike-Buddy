"""Per-request GraphHopper custom models for riders who want or avoid climbs.

GraphHopper merges these with the profile's model. They can only lower
priorities, so "seek" makes flat roads less attractive rather than making
hills more attractive. average_slope is in percent, positive uphill in the
direction of travel.

Measured on 25 km loops from two Montreal starts (8 seeds each): the seek
model raised median climbing from 257 to 280 m and 373 to 403 m, and the
hilliest seed reached 407 and 697 m. Picking the best seed matters as much
as the model, so the plan tool does both.
"""

from app.models.spec import Climbing
from app.routing.graphhopper import CustomModel

SEEK_CLIMBS: CustomModel = {
    "priority": [{"if": "average_slope > -2 && average_slope < 2", "multiply_by": "0.25"}]
}
AVOID_CLIMBS: CustomModel = {
    "priority": [
        {"if": "average_slope > 4", "multiply_by": "0.3"},
        {"if": "average_slope < -4", "multiply_by": "0.6"},
    ]
}


def climbing_model(climbing: Climbing) -> CustomModel | None:
    return {Climbing.SEEK: SEEK_CLIMBS, Climbing.AVOID: AVOID_CLIMBS}.get(climbing)
