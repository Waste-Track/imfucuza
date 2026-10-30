// Shapes the Engine returns. Source of truth: the backend's /openapi.json.

export type Role = "household" | "rider" | "supervisor" | "admin";

export type Me = {
  role: Role | null;
  user_id: string | null;
  name: string | null;
  // "pending" for a rider waiting for a supervisor to approve the application.
  status: "pending" | "active" | "suspended" | "deleted" | null;
  household_id: string | null;
  rider_id: string | null;
  on_duty: boolean | null;
  consent_version: string;
  leaderboard_consent_version: string;
  refuse_fee_pesewas: number;
};

export type Offering = "refuse" | "plastic";

export type PickupStatus =
  | "awaiting_payment"
  | "pending_dispatch"
  | "offered"
  | "assigned"
  | "arrived"
  | "location_issue"
  | "verifying"
  | "rider_review"
  | "awaiting_pin"
  | "disputed"
  | "completed"
  | "rejected"
  | "unconfirmed"
  | "cancelled"
  | "expired"
  | "failed"
  | "refunded";

export type PickupView = {
  id: string;
  offering: Offering;
  status: PickupStatus;
  fee_pesewas: number | null;
  declared_type: PlasticType | null;
  weight_g: number | null;
  points_due: number | null;
  verification_outcome: string;
  photo_wanted: boolean;
  // Only while the rider is on the way or at the door.
  rider_name: string | null;
  rider_phone: string | null;
  requested_at: string;
  completed_at: string | null;
};

export type PlasticType = "pet" | "hdpe" | "pp" | "mixed_plastic";

export type Network = "mtn" | "telecel" | "airteltigo";

export type PaymentStarted = {
  reference: string;
  state: "pending" | "succeeded" | "failed" | string;
  display_text: string | null;
  needs_otp: boolean;
};

export type Points = { available: number; pending: number };

export type LeaderboardView = {
  zone: { id: string; name: string };
  month: string;
  top: { rank: number; name: string; points: number }[];
  you: { rank: number; points: number; on_board: boolean } | null;
};

export type Product = {
  id: string;
  name: string;
  points_cost: number;
  face_value_pesewas: number;
};

export type Voucher = {
  id: string;
  product: string;
  points: number;
  status: string;
  code: string | null;
  issued_at: string;
  expires_at: string;
  used_at: string | null;
  used_at_shop: string | null;
};

export type OfferView = {
  id: string;
  pickup_id: string;
  offering: Offering;
  distance_m: number;
  expires_at: string;
  earning_pesewas: number | null;
  pay_per_kg_pesewas: number | null;
};

export type PastJob = {
  pickup_id: string;
  offering: Offering;
  status: PickupStatus;
  finished_at: string;
  earned_pesewas: number;
};

export type Payout = {
  id: string;
  amount_pesewas: number;
  status: "pending_approval" | "approved" | "recorded" | "rejected";
  created_at: string;
  recorded_at: string | null;
};

export type Earnings = {
  owed_pesewas: number;
  today_pesewas: number;
  week_pesewas: number;
  jobs_today: number;
  payout_phone: string | null;
  payouts: Payout[];
};

export type JobView = {
  pickup_id: string;
  offering: Offering;
  status: PickupStatus;
  lat: number;
  lng: number;
  address_text: string | null;
  household_phone: string | null;
  earning_pesewas: number | null;
  declared_type: PlasticType | null;
  weight_g: number | null;
  verification_outcome: string;
};
