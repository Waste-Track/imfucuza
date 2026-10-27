import type { Offering, PickupStatus } from "./types";

export type Step = { label: string; state: "done" | "current" | "todo" };

const ENDED: readonly PickupStatus[] = [
  "completed",
  "rejected",
  "unconfirmed",
  "cancelled",
  "expired",
  "failed",
  "refunded",
];

export function isEnded(status: PickupStatus): boolean {
  return ENDED.includes(status);
}

/** One line for the household on what is happening now. */
export function headline(status: PickupStatus, offering: Offering): string {
  switch (status) {
    case "awaiting_payment":
      return "Approve the payment on your phone";
    case "pending_dispatch":
    case "offered":
      return "Finding a rider near you";
    case "assigned":
      return "A rider is on the way";
    case "arrived":
      return offering === "plastic" ? "The rider is weighing your plastic" : "The rider is at your door";
    case "location_issue":
      return "The rider can't find you. Expect a call";
    case "verifying":
      return "Checking the photo";
    case "rider_review":
      return "The rider is checking your plastic";
    case "awaiting_pin":
      return "Give the rider your PIN";
    case "disputed":
      return "A supervisor is looking into it";
    case "completed":
      return offering === "plastic" ? "Plastic collected" : "Pickup complete";
    case "rejected":
      return "Plastic not accepted";
    case "unconfirmed":
      return "The PIN was never entered";
    case "cancelled":
      return "Cancelled";
    case "expired":
      return "No rider was free";
    case "failed":
      return "The pickup could not be completed";
    case "refunded":
      return "Refunded";
  }
}

/** What happens to the household's money or points once a pickup has ended. */
export function outcomeNote(status: PickupStatus, offering: Offering): string | null {
  const money = offering === "refuse";
  switch (status) {
    case "cancelled":
    case "expired":
    case "failed":
      return money ? "You were not charged. Any hold on your mobile money is released." : null;
    case "refunded":
      return "Your payment has been sent back to your mobile money.";
    case "unconfirmed":
      return "If the rider did collect, file a complaint and a supervisor will decide.";
    case "rejected":
      return "No points for this load. If you disagree, file a complaint.";
    default:
      return null;
  }
}

const STAGES: { label: (o: Offering) => string; reached: readonly PickupStatus[] }[] = [
  { label: () => "Requested", reached: [] },
  {
    label: (o) => (o === "refuse" ? "Paid and waiting for a rider" : "Waiting for a rider"),
    reached: ["pending_dispatch", "offered"],
  },
  { label: () => "Rider on the way", reached: ["assigned", "location_issue"] },
  {
    label: (o) => (o === "refuse" ? "Rider at your door" : "Weighed and photo checked"),
    reached: ["arrived", "verifying", "rider_review"],
  },
  {
    label: (o) => (o === "refuse" ? "Collected, PIN given" : "PIN given, points added"),
    reached: ["awaiting_pin", "disputed", "completed"],
  },
];

/** The timeline on the status screen, for a pickup still under way or complete. */
export function steps(status: PickupStatus, offering: Offering): Step[] {
  const index = STAGES.findIndex((s) => s.reached.includes(status));
  const current = status === "awaiting_payment" ? 0 : index;
  return STAGES.map((stage, i) => ({
    label: stage.label(offering),
    state:
      status === "completed" || i < current ? "done" : i === current ? "current" : "todo",
  }));
}
