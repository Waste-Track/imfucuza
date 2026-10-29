import { describe, expect, it } from "vitest";
import { headline, isEnded, outcomeNote, steps } from "./pickupStatus";
import type { PickupStatus } from "./types";

const ALL: PickupStatus[] = [
  "awaiting_payment",
  "pending_dispatch",
  "offered",
  "assigned",
  "arrived",
  "location_issue",
  "verifying",
  "rider_review",
  "awaiting_pin",
  "disputed",
  "completed",
  "rejected",
  "unconfirmed",
  "cancelled",
  "expired",
  "failed",
  "refunded",
];

describe("pickup status", () => {
  it("has a headline for every status the Engine can return", () => {
    for (const status of ALL) {
      expect(headline(status, "refuse")).toBeTruthy();
      expect(headline(status, "plastic")).toBeTruthy();
    }
  });

  it("marks only final statuses as ended", () => {
    expect(isEnded("completed")).toBe(true);
    expect(isEnded("refunded")).toBe(true);
    expect(isEnded("awaiting_pin")).toBe(false);
    expect(isEnded("disputed")).toBe(false);
  });

  it("puts a paid refuse pickup with a rider on the way at the third step", () => {
    expect(steps("assigned", "refuse").map((s) => s.state)).toEqual([
      "done",
      "done",
      "current",
      "todo",
      "todo",
    ]);
  });

  it("starts at the first step while payment is awaited", () => {
    expect(steps("awaiting_payment", "refuse")[0].state).toBe("current");
  });

  it("marks every step done once complete", () => {
    expect(steps("completed", "plastic").every((s) => s.state === "done")).toBe(true);
  });

  it("tells a household it was not charged when nobody came", () => {
    expect(outcomeNote("expired", "refuse")).toMatch(/not charged/);
    expect(outcomeNote("expired", "plastic")).toBeNull();
  });
});
