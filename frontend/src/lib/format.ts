import type { PlasticType } from "./types";

/** 1000 pesewas -> "GH₵ 10.00" */
export function cedis(pesewas: number): string {
  return `GH₵ ${(pesewas / 100).toFixed(2)}`;
}

export function kg(grams: number): string {
  const value = grams / 1000;
  return `${value.toFixed(value < 10 ? 1 : 0)} kg`;
}

export function distance(metres: number): string {
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m` : `${(metres / 1000).toFixed(1)} km`;
}

/**
 * A Ghanaian number in E.164, the way the Engine stores it: 024 123 4567,
 * 233241234567 and +233 24 123 4567 all become +233241234567.
 * Returns null for anything that isn't a Ghanaian mobile number.
 */
export function ghanaPhone(raw: string): string | null {
  let digits = raw.replace(/[\s\-()]/g, "");
  if (digits.startsWith("+")) digits = digits.slice(1);
  else if (digits.startsWith("00")) digits = digits.slice(2);
  if (!/^\d+$/.test(digits)) return null;
  if (digits.startsWith("0")) digits = "233" + digits.slice(1);
  if (!/^233[1-9]\d{8}$/.test(digits)) return null;
  return "+" + digits;
}

export const PLASTIC_LABELS: Record<PlasticType, string> = {
  pet: "PET bottles (water, soft drinks)",
  hdpe: "HDPE (gallons, detergent bottles)",
  pp: "PP (buckets, containers)",
  mixed_plastic: "Mixed plastic",
};

export function minutesLeft(until: string, now: number = Date.now()): number {
  return Math.max(0, Math.ceil((new Date(until).getTime() - now) / 60_000));
}
