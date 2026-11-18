import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { LineString } from "geojson";

import FitnessChart from "./FitnessChart";
import {
  deleteRide,
  getProfile,
  getRide,
  getTraining,
  listRides,
  saveProfile,
  uploadRides,
  type Ride,
  type RiderProfile,
  type Suggestion,
  type TrainingOverview,
  type UploadResult,
} from "@/lib/training";

const RIDES_SHOWN = 12;
const METHOD_LABEL = { power: "power", heart_rate: "HR", estimate: "est." } as const;
const SUGGESTION_TITLE: Record<Suggestion["kind"], string> = {
  rest: "Rest day",
  recovery: "Easy recovery ride",
  endurance: "Steady endurance ride",
  long_or_hilly: "Long or hilly ride",
  ease_back_in: "Ease back in",
};

type Props = {
  /** Show a recorded ride's line on the map, or clear it. */
  onShowRide: (geometry: LineString | null) => void;
  /** Hand a request to the planner, for the rider to add a start. */
  onPlan: (message: string) => void;
};

function formatMinutes(seconds: number): string {
  const minutes = Math.round(seconds / 60);
  const hours = Math.floor(minutes / 60);
  return hours ? `${hours} h ${minutes % 60} min` : `${minutes} min`;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function suggestionText(s: Suggestion): string {
  const parts = [];
  if (s.target_distance_km) parts.push(`about ${s.target_distance_km} km`);
  if (s.duration_min) parts.push(`${formatMinutes(s.duration_min * 60)}`);
  if (s.climbing === "avoid") parts.push("flat");
  if (s.climbing === "seek") parts.push("with climbs");
  return parts.join(", ");
}

/** Trainer mode: upload rides, see fitness and form, and plan a ride that suits them. */
export default function TrainingPanel({ onShowRide, onPlan }: Props) {
  const [overview, setOverview] = useState<TrainingOverview | null>(null);
  const [rides, setRides] = useState<Ride[]>([]);
  const [profile, setProfile] = useState<RiderProfile>({ ftp_w: null, max_hr: null, resting_hr: null });
  const [error, setError] = useState<string | null>(null);
  const [uploads, setUploads] = useState<UploadResult[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [shownRide, setShownRide] = useState<number | null>(null);
  const [profileNote, setProfileNote] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [o, r] = await Promise.all([getTraining(), listRides()]);
      setOverview(o);
      setRides(r);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not load your training.");
    }
  }, []);

  useEffect(() => {
    void refresh();
    getProfile().then(setProfile, () => {});
  }, [refresh]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setBusy("Reading rides…");
    setUploads([]);
    try {
      const { results } = await uploadRides(Array.from(files));
      setUploads(results);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not upload those files.");
    } finally {
      setBusy(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function handleProfile(event: FormEvent) {
    event.preventDefault();
    setBusy("Saving…");
    setProfileNote(null);
    try {
      const { rides_updated } = await saveProfile(profile);
      setProfileNote(
        rides_updated ? `Saved. Training load recalculated for ${rides_updated} rides.` : "Saved.",
      );
      await refresh();
    } catch (reason) {
      setProfileNote(reason instanceof Error ? reason.message : "Could not save.");
    } finally {
      setBusy(null);
    }
  }

  async function handleShowRide(ride: Ride) {
    if (shownRide === ride.id) {
      setShownRide(null);
      onShowRide(null);
      return;
    }
    try {
      const detail = await getRide(ride.id);
      setShownRide(ride.id);
      onShowRide(detail.geometry);
    } catch {
      setError("Could not load that ride.");
    }
  }

  async function handleDelete(ride: Ride) {
    if (!window.confirm(`Delete "${ride.name ?? ride.filename}"? This cannot be undone.`)) return;
    try {
      await deleteRide(ride.id);
      if (shownRide === ride.id) {
        setShownRide(null);
        onShowRide(null);
      }
      await refresh();
    } catch {
      setError("Could not delete that ride.");
    }
  }

  function numberField(key: keyof RiderProfile, label: string, unit: string, min: number, max: number) {
    return (
      <label className="profile-field">
        <span>{label}</span>
        <input
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          value={profile[key] ?? ""}
          onChange={(e) => setProfile({ ...profile, [key]: e.target.value ? Number(e.target.value) : null })}
        />
        <span className="profile-unit">{unit}</span>
      </label>
    );
  }

  const status = overview?.status;
  const suggestion = status?.suggestion ?? null;
  const visibleRides = showAll ? rides : rides.slice(0, RIDES_SHOWN);

  return (
    <div className="training">
      {error && (
        <p className="panel-error" role="alert">
          {error}
        </p>
      )}

      {status && status.rides > 0 && (
        <section className="training-status" aria-label="Form">
          <div className="form-figure">
            <span className="form-number">{Math.round(status.form) > 0 ? `+${Math.round(status.form)}` : Math.round(status.form)}</span>
            <span>
              <strong>Form: {status.form_label}</strong>
              <br />
              <span className="panel-note-inline">
                fitness {Math.round(status.fitness)} · fatigue {Math.round(status.fatigue)}
              </span>
            </span>
          </div>
          {suggestion && (
            <div className="suggestion">
              <strong>{SUGGESTION_TITLE[suggestion.kind]}</strong>
              {suggestionText(suggestion) && <>: {suggestionText(suggestion)}</>}. <span>{capitalise(suggestion.reason)}.</span>
              {suggestion.kind !== "rest" && (
                <button
                  type="button"
                  className="panel-button"
                  onClick={() => onPlan("Plan today's ride based on my training, from ")}
                >
                  Plan this ride
                </button>
              )}
            </div>
          )}
        </section>
      )}

      {overview && overview.fitness.length > 1 && (
        <section className="training-section">
          <h2>Fitness and fatigue, last {overview.fitness.length} days</h2>
          <FitnessChart series={overview.fitness} />
          <details className="weeks-table">
            <summary>Weekly totals</summary>
            <table>
              <thead>
                <tr>
                  <th scope="col">Week of</th>
                  <th scope="col">Rides</th>
                  <th scope="col">km</th>
                  <th scope="col">Time</th>
                  <th scope="col">Load</th>
                </tr>
              </thead>
              <tbody>
                {[...overview.weeks].reverse().map((w) => (
                  <tr key={w.week_start}>
                    <td>{new Date(`${w.week_start}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</td>
                    <td>{w.rides}</td>
                    <td>{Math.round(w.distance_km)}</td>
                    <td>{formatMinutes(w.moving_h * 3600)}</td>
                    <td>{Math.round(w.load)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      )}

      <section className="training-section">
        <h2>Add rides</h2>
        <label
          className="drop-zone"
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault();
            void handleFiles(e.dataTransfer.files);
          }}
        >
          <input
            ref={fileInput}
            type="file"
            accept=".gpx,.fit"
            multiple
            onChange={(e) => void handleFiles(e.target.files)}
          />
          <span>{busy ?? "Drop GPX or FIT files here, or click to choose"}</span>
        </label>
        {uploads.length > 0 && (
          <ul className="upload-results">
            {uploads.map((u, i) => (
              <li key={i} data-status={u.status}>
                {u.filename}: {u.status === "added" ? "added" : u.reason}
              </li>
            ))}
          </ul>
        )}
        {status && status.rides === 0 && (
          <p className="panel-note">
            Export rides from Garmin Connect, Strava or your bike computer. Nothing leaves this app.
          </p>
        )}
      </section>

      <form className="training-section" onSubmit={handleProfile}>
        <h2>Your profile</h2>
        <p className="panel-note">
          With FTP, training load comes from power; with max and resting heart rate, from heart rate.
          Otherwise it is estimated from time and climbing.
        </p>
        <div className="profile-fields">
          {numberField("ftp_w", "FTP", "W", 50, 600)}
          {numberField("max_hr", "Max HR", "bpm", 100, 230)}
          {numberField("resting_hr", "Resting HR", "bpm", 30, 100)}
        </div>
        <button type="submit" className="panel-button" disabled={busy !== null}>
          Save profile
        </button>
        {profileNote && <p className="panel-note">{profileNote}</p>}
      </form>

      {rides.length > 0 && (
        <section className="training-section">
          <h2>Rides ({rides.length})</h2>
          <ul className="ride-list">
            {visibleRides.map((ride) => (
              <li key={ride.id} data-shown={shownRide === ride.id}>
                <button type="button" className="ride-row" onClick={() => void handleShowRide(ride)}>
                  <span className="ride-name">
                    {new Date(ride.started_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}{" "}
                    · {ride.name ?? ride.filename}
                  </span>
                  <span className="ride-figures">
                    {(ride.distance_m / 1000).toFixed(1)} km · {formatMinutes(ride.moving_s)} · load{" "}
                    {Math.round(ride.load)} ({METHOD_LABEL[ride.load_method]})
                  </span>
                </button>
                <button
                  type="button"
                  className="ride-delete"
                  aria-label={`Delete ${ride.name ?? ride.filename}`}
                  onClick={() => void handleDelete(ride)}
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          {rides.length > RIDES_SHOWN && (
            <button type="button" className="link-button" onClick={() => setShowAll(!showAll)}>
              {showAll ? "Show fewer" : `Show all ${rides.length}`}
            </button>
          )}
          <p className="panel-note">Click a ride to see it on the map.</p>
        </section>
      )}
    </div>
  );
}
