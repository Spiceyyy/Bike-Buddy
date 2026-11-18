from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.rate_limit import limit_requests
from app.db.feedback import FeedbackError, FeedbackStore
from app.models.feedback import FeedbackRequest, FeedbackResponse

router = APIRouter()


def get_feedback_store(request: Request) -> FeedbackStore:
    store = getattr(request.app.state, "feedback_store", None)
    if store is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Feedback is unavailable.")
    return store


@router.post(
    "/api/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(limit_requests("feedback_limiter", "Too many ratings. Please wait a little."))
    ],
)
async def save_feedback(
    body: FeedbackRequest, store: Annotated[FeedbackStore, Depends(get_feedback_store)]
) -> FeedbackResponse:
    """Store a rider's thumbs up or down on the route they are looking at."""
    try:
        return FeedbackResponse(id=await store.save(body))
    except FeedbackError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Could not save your feedback. Please try again."
        ) from exc
