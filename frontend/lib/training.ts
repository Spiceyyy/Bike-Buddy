import type { LineString } from "geojson";

import { readErrorMessage } from "./api";
import { API_URL } from "./config";

export type LoadMethod = "power" | "heart_rate" | "estimate";

export type Ride = {
  id: number;
  name: string | null;
  filename: string;
  started_at: string;
  moving_s: number;
  distance_m: number;
  ascent_m: number | null;
  avg_speed_kmh: number;
  avg_hr: number | null;
  normalized_power: number | null;
  load: number;
  load_method: LoadMethod;
};

export type DayFitness = { day: string; load: number; fitness: number; fatigue: number; form: number };

export type WeekSummary = {
  week_start: string;
  rides: number;
  distance_km: number;
  moving_h: number;
  ascent_m: number;
  load: number;
};

export type Suggestion = {
  kind: "rest" | "recovery" | "endurance" | "long_or_hilly" | "ease_back_in";
  reason: string;
  target_distance_km: number | null;
  climbing: "any" | "avoid" | "seek";
  duration_min: number | null;
};

export type TrainingOverview = {
  status: {
    rides: number;
    fitness: number;
    fatigue: number;
    form: number;
    form_label: "no data" | "fresh" | "neutral" | "tired" | "very tired";
    days_since_last_ride: number | null;
    suggestion: Suggestion | null;
    load_methods: Partial<Record<LoadMethod, number>>;
  };
  fitness: DayFitness[];
  weeks: WeekSummary[];
};

export type RiderProfile = { ftp_w: number | null; max_hr: number | null; resting_hr: number | null };

export type UploadResult = {
  filename: string;
  status: "added" | "duplicate" | "error";
  reason: string | null;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, init);
  if (!response.ok) throw new Error(await readErrorMessage(response));
  return response.status === 204 ? (undefined as T) : response.json();
}

export const getTraining = () => request<TrainingOverview>("/api/training");
export const listRides = () => request<Ride[]>("/api/rides");
export const getRide = (id: number) => request<Ride & { geometry: LineString }>(`/api/rides/${id}`);
export const deleteRide = (id: number) => request<void>(`/api/rides/${id}`, { method: "DELETE" });
export const getProfile = () => request<RiderProfile>("/api/profile");

export function saveProfile(profile: RiderProfile) {
  return request<{ profile: RiderProfile; rides_updated: number }>("/api/profile", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
}

export function uploadRides(files: File[]) {
  const body = new FormData();
  for (const file of files) body.append("files", file);
  return request<{ results: UploadResult[] }>("/api/rides", { method: "POST", body });
}
