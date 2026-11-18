"""Trainer mode: upload rides, see fitness and form, set the rider's profile.

There are no accounts, so all of this belongs to whoever runs the app.
Production turns it off by default (TRAINING_ENABLED=false).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.db.rides import RideStoreError
from app.models.training import Ride, RideDetail, RiderProfile
from app.training.service import Trainer, TrainingOverview, UploadResult

router = APIRouter()

MAX_FILES = 50
MAX_FILE_BYTES = 25 * 1024 * 1024


def get_trainer(request: Request) -> Trainer:
    trainer = getattr(request.app.state, "trainer", None)
    if trainer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Trainer mode is turned off.")
    return trainer


TrainerDep = Annotated[Trainer, Depends(get_trainer)]


class UploadResponse(BaseModel):
    results: list[UploadResult]


def _unavailable(exc: RideStoreError) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Ride storage is unavailable.")


@router.post("/api/rides", response_model=UploadResponse)
async def upload_rides(files: list[UploadFile], trainer: TrainerDep) -> UploadResponse:
    """Add recorded rides from GPX or FIT files. Each file is reported on separately."""
    if len(files) > MAX_FILES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Upload at most {MAX_FILES} files at a time.")
    results = []
    for file in files:
        name = (file.filename or "ride")[:200]
        data = await file.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            results.append(UploadResult(filename=name, status="error", reason="This file is over 25 MB."))
            continue
        try:
            results.append(await trainer.upload(name, data))
        except RideStoreError as exc:
            raise _unavailable(exc) from exc
    return UploadResponse(results=results)


@router.get("/api/rides", response_model=list[Ride])
async def list_rides(trainer: TrainerDep) -> list[Ride]:
    try:
        return await trainer.store.list_rides()
    except RideStoreError as exc:
        raise _unavailable(exc) from exc


@router.get("/api/rides/{ride_id}", response_model=RideDetail)
async def get_ride(ride_id: int, trainer: TrainerDep) -> RideDetail:
    try:
        ride = await trainer.store.get(ride_id)
    except RideStoreError as exc:
        raise _unavailable(exc) from exc
    if ride is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ride.")
    return ride


@router.delete("/api/rides/{ride_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ride(ride_id: int, trainer: TrainerDep) -> Response:
    try:
        deleted = await trainer.store.delete(ride_id)
    except RideStoreError as exc:
        raise _unavailable(exc) from exc
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such ride.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/training", response_model=TrainingOverview)
async def training_overview(trainer: TrainerDep) -> TrainingOverview:
    """Fitness, fatigue and form for the last 90 days, weekly totals and today's suggestion."""
    try:
        return await trainer.overview()
    except RideStoreError as exc:
        raise _unavailable(exc) from exc


@router.get("/api/profile", response_model=RiderProfile)
async def get_profile(trainer: TrainerDep) -> RiderProfile:
    try:
        return await trainer.store.profile()
    except RideStoreError as exc:
        raise _unavailable(exc) from exc


class ProfileResponse(BaseModel):
    profile: RiderProfile
    rides_updated: int


@router.put("/api/profile", response_model=ProfileResponse)
async def save_profile(body: RiderProfile, trainer: TrainerDep) -> ProfileResponse:
    """Save FTP and heart rates, and recompute every ride's training load with them."""
    if body.max_hr and body.resting_hr and body.resting_hr >= body.max_hr:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Resting heart rate must be below maximum.")
    try:
        updated = await trainer.save_profile(body)
    except RideStoreError as exc:
        raise _unavailable(exc) from exc
    return ProfileResponse(profile=body, rides_updated=updated)
