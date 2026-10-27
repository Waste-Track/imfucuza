import { supabase } from "./supabase";
import type {
  JobView,
  LeaderboardView,
  Me,
  Network,
  OfferView,
  PaymentStarted,
  PickupView,
  Points,
  Product,
  Voucher,
} from "./types";

const BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

export class ApiError extends Error {
  readonly status: number;
  readonly body: Record<string, unknown>;

  constructor(status: number, body: Record<string, unknown>) {
    super(describe(status, body));
    this.status = status;
    this.body = body;
  }
}

/** The Engine's `detail` is written for people, so show it as it is. */
export function describe(status: number, body: Record<string, unknown>): string {
  const detail = body.detail;
  if (typeof detail === "string") return capitalise(detail);
  // FastAPI validation errors: a list of {msg} objects.
  if (Array.isArray(detail) && detail.length && typeof detail[0]?.msg === "string") {
    return capitalise(detail[0].msg);
  }
  if (status === 0) return "No connection. Check your data or Wi-Fi and try again.";
  if (status >= 500) return "Something went wrong on our side. Try again in a minute.";
  return `Request failed (${status})`;
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

type Options = {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  json?: unknown;
  body?: Blob;
  query?: Record<string, string | number>;
  idempotencyKey?: string;
};

export async function request<T>(path: string, options: Options = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const headers: Record<string, string> = {};
  if (data.session) headers.Authorization = `Bearer ${data.session.access_token}`;
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;

  let body: BodyInit | undefined;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.body) {
    headers["Content-Type"] = options.body.type || "application/octet-stream";
    body = options.body;
  }

  const query = options.query
    ? "?" + new URLSearchParams(Object.entries(options.query).map(([k, v]) => [k, String(v)]))
    : "";

  let response: Response;
  try {
    response = await fetch(`${BASE}${path}${query}`, {
      method: options.method ?? "GET",
      headers,
      body,
    });
  } catch {
    throw new ApiError(0, {});
  }
  if (response.status === 204) return undefined as T;
  const parsed = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(response.status, parsed);
  return parsed as T;
}

export const api = {
  me: () => request<Me>("/v1/me"),

  // Households
  register: (body: {
    consent_version: string;
    name?: string;
    address_text?: string;
    lat?: number;
    lng?: number;
  }) => request<{ household_id: string }>("/v1/households/me", { method: "PUT", json: body }),
  requestPickup: (
    body: { offering: "refuse" | "plastic"; declared_type?: string; lat?: number; lng?: number },
    idempotencyKey: string,
  ) => request<PickupView>("/v1/pickups", { method: "POST", json: body, idempotencyKey }),
  pickups: () => request<PickupView[]>("/v1/pickups"),
  pickup: (id: string) => request<PickupView>(`/v1/pickups/${id}`),
  pay: (id: string, network: Network) =>
    request<PaymentStarted>(`/v1/pickups/${id}/payment`, { method: "POST", json: { network } }),
  payOtp: (id: string, reference: string, otp: string) =>
    request<PaymentStarted>(`/v1/pickups/${id}/payment/otp`, {
      method: "POST",
      json: { reference, otp },
    }),
  cancel: (id: string) =>
    request<{ refund_started: boolean }>(`/v1/pickups/${id}/cancel`, { method: "POST" }),
  resendPin: (id: string) => request(`/v1/pickups/${id}/pin/resend`, { method: "POST" }),
  confirm: (id: string, pin: string) =>
    request(`/v1/pickups/${id}/confirm`, { method: "POST", json: { pin } }),
  householdPhoto: (id: string, weightG: number, photo: Blob) =>
    request<{ status: string }>(`/v1/pickups/${id}/plastic/photo`, {
      method: "POST",
      body: photo,
      query: { weight_g: weightG },
    }),
  complain: (id: string, details: string) =>
    request<{ status: string }>(`/v1/pickups/${id}/complaints`, {
      method: "POST",
      json: { details },
    }),
  points: () => request<Points>("/v1/points/me"),
  leaderboard: () => request<LeaderboardView>("/v1/leaderboard"),
  joinLeaderboard: (name: string | null, consentVersion?: string) =>
    request("/v1/households/me/leaderboard", {
      method: "PUT",
      json: { name, consent_version: consentVersion },
    }),
  catalogue: () => request<Product[]>("/v1/vouchers/catalog"),
  vouchers: () => request<Voucher[]>("/v1/vouchers/me"),
  buyVoucher: (productId: string, idempotencyKey: string) =>
    request<Voucher>("/v1/vouchers", {
      method: "POST",
      json: { product_id: productId },
      idempotencyKey,
    }),

  // Riders
  setDuty: (onDuty: boolean) =>
    request<{ on_duty: boolean }>("/v1/riders/me/duty", {
      method: "PUT",
      json: { on_duty: onDuty },
    }),
  reportLocations: (fixes: Fix[]) =>
    request("/v1/riders/me/locations", { method: "POST", json: { fixes } }),
  offers: () => request<OfferView[]>("/v1/riders/me/offers"),
  accept: (offerId: string) =>
    request<{ pickup_id: string }>(`/v1/offers/${offerId}/accept`, { method: "POST" }),
  decline: (offerId: string) => request(`/v1/offers/${offerId}/decline`, { method: "POST" }),
  jobs: () => request<JobView[]>("/v1/riders/me/jobs"),
  arrive: (pickupId: string, fix: Fix) =>
    request(`/v1/pickups/${pickupId}/arrive`, { method: "POST", json: fix }),
  collected: (pickupId: string) => request(`/v1/pickups/${pickupId}/collected`, { method: "POST" }),
  submitPlastic: (pickupId: string, weightG: number, photo: Blob) =>
    request<{ status: string }>(`/v1/pickups/${pickupId}/plastic`, {
      method: "POST",
      body: photo,
      query: { weight_g: weightG },
    }),
  riderDecision: (pickupId: string, accept: boolean) =>
    request<{ status: string }>(`/v1/pickups/${pickupId}/rider-decision`, {
      method: "POST",
      json: { accept },
    }),
  riderPin: (pickupId: string, pin: string) =>
    request(`/v1/pickups/${pickupId}/pin`, { method: "POST", json: { pin } }),
};

export type Fix = {
  lat: number;
  lng: number;
  accuracy_m?: number;
  recorded_at: string;
};
