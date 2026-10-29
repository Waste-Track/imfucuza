import { request } from "../../lib/api";

// Supervisor routes under /v1/admin. Shapes follow the backend's supervision
// queries; fields the console doesn't show are left out.

export type ReviewType =
  | "household_complaint"
  | "low_confidence_pattern"
  | "rider_override_rate"
  | "manual_verification"
  | "recycler_contamination"
  | "audit_sample"
  | "pin_issue"
  | "dispatch_stalled"
  | "payment_mismatch";

export const REVIEW_LABELS: Record<ReviewType, string> = {
  household_complaint: "Household complaint",
  low_confidence_pattern: "Repeated unsure photos",
  rider_override_rate: "Rider overrides often",
  manual_verification: "Check plastic by hand",
  recycler_contamination: "Recycler reported contamination",
  audit_sample: "Audit sample",
  pin_issue: "PIN problem",
  dispatch_stalled: "Dispatch stalled",
  payment_mismatch: "Payment mismatch",
};

export type ReviewSummary = {
  id: string;
  type: ReviewType;
  status: "open" | "resolved";
  pickup_id: string | null;
  created_at: string;
  claimed_at: string | null;
  reason: string | null;
  assigned_to: string | null;
  assigned_to_name: string | null;
};

export type Photo = { id: string; source?: string; pickup_id?: string; url: string | null; created_at: string };

export type ReviewDetail = {
  item: ReviewSummary & {
    payload: Record<string, unknown>;
    resolution: string | null;
    resolved_at: string | null;
    rider_id: string | null;
    household_id: string | null;
  };
  pickup?: {
    id: string;
    offering: "refuse" | "plastic";
    status: string;
    fee_pesewas: number | null;
    declared_type: string | null;
    weight_g: number | null;
    points_due: number | null;
    rider_pay_pesewas: number | null;
    verification_outcome: string;
    requested_at: string;
    completed_at: string | null;
    assigned_rider_id: string | null;
  } | null;
  timeline?: { occurred_at: string; name: string; actor_type: string; payload: Record<string, unknown> }[];
  payments?: { direction: string; reference: string; status: string; amount_pesewas: number; created_at: string }[];
  pins?: { status: string; attempts: number; expires_at: string; confirmed_via: string | null; created_at: string }[];
  offers?: { rider_id: string; distance_m: number; channel: string; sent_at: string; response: string | null }[];
  messages?: { template: string; body_masked: string; status: string; created_at: string }[];
  photos?: Photo[];
  possible_copies_of?: Photo[];
  verification?: {
    stage: string;
    decision: string;
    confidence: number | null;
    reason: string | null;
    model_version: string | null;
    is_override: boolean;
    created_at: string;
  }[];
};

export type RiderRow = {
  id: string;
  name: string | null;
  phone_e164: string;
  status: "pending" | "active" | "suspended" | "deleted";
  channel: "pwa" | "ussd";
  on_duty: boolean;
  tricycle_plate: string | null;
  work_area: string | null;
  missed_offers: number;
  payout_msisdn: string | null;
  payout_msisdn_changed_at: string | null;
  owed_pesewas: number;
  last_location_at: string | null;
};

export type PayoutStatus = "pending_approval" | "approved" | "recorded" | "rejected";

export type PayoutRow = {
  id: string;
  rider_id: string;
  rider_name: string | null;
  amount_pesewas: number;
  destination_msisdn: string;
  status: PayoutStatus;
  momo_reference: string | null;
  created_at: string;
  requested_by: string;
  requested_by_name: string | null;
  approved_by: string | null;
};

export type ComplaintOutcome = "reissue_pin" | "refund" | "grant_points" | "dismiss";

export type DecisionRow = {
  id: string;
  pickup_id: string;
  outcome: ComplaintOutcome;
  amount: number | null;
  note: string;
  status: string;
  created_at: string;
  decided_by: string;
  decided_by_name: string | null;
};

export type Zone = {
  id: number;
  name: string;
  lat: number;
  lng: number;
  radius_m: number;
  ussd_index: number;
  landmarks: { id: number; name: string; lat: number; lng: number; ussd_index: number }[];
};

export type ProductRow = {
  id: string;
  name: string;
  points_cost: number;
  face_value_pesewas: number;
  active: boolean;
};

export type ShopReportRow = { shop_id: string; shop: string; vouchers: number; face_value_pesewas: number };

const A = "/v1/admin";

export const admin = {
  reviewItems: (status: "open" | "resolved", type?: string) =>
    request<ReviewSummary[]>(`${A}/review-items`, {
      query: { status, limit: 200, ...(type ? { type } : {}) },
    }),
  reviewItem: (id: string) => request<ReviewDetail>(`${A}/review-items/${id}`),
  claim: (id: string) => request(`${A}/review-items/${id}/claim`, { method: "POST" }),
  resolve: (id: string, resolution: string) =>
    request(`${A}/review-items/${id}/resolve`, { method: "POST", json: { resolution } }),

  reissuePin: (pickupId: string) => request(`${A}/pickups/${pickupId}/reissue-pin`, { method: "POST" }),
  cancel: (pickupId: string, reason: string) =>
    request(`${A}/pickups/${pickupId}/cancel`, { method: "POST", json: { reason } }),
  redispatch: (pickupId: string) => request(`${A}/pickups/${pickupId}/redispatch`, { method: "POST" }),
  assign: (pickupId: string, riderId: string) =>
    request(`${A}/pickups/${pickupId}/assign`, { method: "POST", json: { rider_id: riderId } }),
  checkMaterial: (pickupId: string, accept: boolean, note: string) =>
    request(`${A}/pickups/${pickupId}/verification`, { method: "POST", json: { accept, note } }),
  decideComplaint: (pickupId: string, outcome: ComplaintOutcome, note: string) =>
    request<{ status?: string }>(`${A}/pickups/${pickupId}/complaint`, {
      method: "POST",
      json: { outcome, note },
    }),

  riders: () => request<RiderRow[]>(`${A}/riders`),
  registerRider: (body: { phone: string; name: string; channel: "pwa" | "ussd"; consent_version: string }) =>
    request<{ rider_id: string }>(`${A}/riders`, { method: "POST", json: body }),
  setRiderActive: (riderId: string, active: boolean) =>
    request(`${A}/riders/${riderId}/status`, { method: "PUT", json: { active } }),
  payable: (riderId: string) => request<{ payable_pesewas: number }>(`${A}/riders/${riderId}/payable`),
  setPayoutNumber: (riderId: string, phone: string) =>
    request(`${A}/riders/${riderId}/payout-number`, { method: "PUT", json: { phone } }),
  requestPayout: (riderId: string, amountPesewas: number) =>
    request<{ payout_id: string; status: string }>(`${A}/riders/${riderId}/payouts`, {
      method: "POST",
      json: { amount_pesewas: amountPesewas },
    }),

  payouts: (status: PayoutStatus) =>
    request<PayoutRow[]>(`${A}/payouts`, { query: { status, limit: 200 } }),
  approvePayout: (id: string) => request(`${A}/payouts/${id}/approve`, { method: "POST" }),
  rejectPayout: (id: string, reason: string) =>
    request(`${A}/payouts/${id}/reject`, { method: "POST", json: { reason } }),
  recordPayout: (id: string, momoReference: string) =>
    request(`${A}/payouts/${id}/record`, { method: "POST", json: { momo_reference: momoReference } }),

  decisions: () =>
    request<DecisionRow[]>(`${A}/complaint-decisions`, { query: { status: "pending_approval", limit: 200 } }),
  approveDecision: (id: string) => request(`${A}/complaint-decisions/${id}/approve`, { method: "POST" }),
  rejectDecision: (id: string) => request(`${A}/complaint-decisions/${id}/reject`, { method: "POST" }),

  zones: () => request<Zone[]>(`${A}/zones`),
  createZone: (body: { name: string; lat: number; lng: number; radius_m: number }) =>
    request(`${A}/zones`, { method: "POST", json: body }),
  addLandmark: (zoneId: number, body: { name: string; lat: number; lng: number }) =>
    request(`${A}/zones/${zoneId}/landmarks`, { method: "POST", json: body }),

  products: () => request<ProductRow[]>(`${A}/voucher-products`),
  addProduct: (body: { name: string; points_cost: number; face_value_pesewas: number }) =>
    request(`${A}/voucher-products`, { method: "POST", json: body }),
  setProductActive: (id: string, active: boolean) =>
    request(`${A}/voucher-products/${id}/active`, { method: "PUT", json: { active } }),
  registerShop: (body: { phone: string; name: string; consent_version: string }) =>
    request<{ shop_id: string }>(`${A}/shops`, { method: "POST", json: body }),
  shopReport: (month: string) => request<ShopReportRow[]>(`${A}/shops/redeemed`, { query: { month } }),
};

export function ago(iso: string, now = Date.now()): string {
  const minutes = Math.round((now - new Date(iso).getTime()) / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function when(iso: string | null): string {
  return iso
    ? new Date(iso).toLocaleString("en-GH", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })
    : "–";
}
