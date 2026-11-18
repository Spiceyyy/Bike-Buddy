import { useState, type FormEvent } from "react";

import { sendFeedback, type FeedbackBody } from "@/lib/api";

type Rating = "up" | "down";
type Status = "idle" | "sending" | "sent" | "error";

type Props = {
  /** Everything about the route on screen except the rating and comment. */
  feedback: Omit<FeedbackBody, "rating" | "comment">;
};

/**
 * Thumbs up or down on the route on screen, with an optional comment.
 * Remount it (with a new key) when the route changes, so a rating always
 * belongs to the route it was given for.
 */
export default function RouteFeedback({ feedback }: Props) {
  const [rating, setRating] = useState<Rating | null>(null);
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<Status>("idle");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!rating) return;
    setStatus("sending");
    try {
      await sendFeedback({ ...feedback, rating, comment: comment.trim() || null });
      setStatus("sent");
    } catch {
      setStatus("error");
    }
  }

  if (status === "sent") {
    return <p className="feedback-thanks">Thanks for rating this route.</p>;
  }

  return (
    <form className="feedback" onSubmit={handleSubmit}>
      <div className="feedback-row">
        <span id="feedback-label">Was this a good route?</span>
        <div role="group" aria-labelledby="feedback-label" className="feedback-thumbs">
          <button
            type="button"
            aria-pressed={rating === "up"}
            aria-label="Good route"
            onClick={() => setRating("up")}
          >
            👍
          </button>
          <button
            type="button"
            aria-pressed={rating === "down"}
            aria-label="Not a good route"
            onClick={() => setRating("down")}
          >
            👎
          </button>
        </div>
      </div>
      {rating && (
        <>
          <textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            maxLength={1000}
            rows={2}
            placeholder={rating === "up" ? "What did you like? (optional)" : "What was wrong? (optional)"}
            aria-label="Comment on this route (optional)"
          />
          <button type="submit" className="panel-button" disabled={status === "sending"}>
            {status === "sending" ? "Sending…" : "Send"}
          </button>
          {status === "error" && (
            <p className="panel-error" role="alert">
              Could not save your feedback. Please try again.
            </p>
          )}
        </>
      )}
    </form>
  );
}
