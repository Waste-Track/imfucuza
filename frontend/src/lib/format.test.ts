import { describe, expect, it } from "vitest";
import { cedis, distance, ghanaPhone, kg, minutesLeft } from "./format";
import { fitWithin } from "./photo";

describe("ghanaPhone", () => {
  it.each(["0241234567", "024 123 4567", "233241234567", "+233241234567", "00233241234567"])(
    "normalises %s like the Engine does",
    (raw) => expect(ghanaPhone(raw)).toBe("+233241234567"),
  );

  it.each(["", "12", "not a number", "+447700900123", "024123456"])("rejects %s", (raw) =>
    expect(ghanaPhone(raw)).toBeNull(),
  );
});

describe("formatting", () => {
  it("shows pesewas as cedis", () => {
    expect(cedis(1000)).toBe("GH₵ 10.00");
    expect(cedis(705)).toBe("GH₵ 7.05");
  });

  it("shows weights and distances", () => {
    expect(kg(3500)).toBe("3.5 kg");
    expect(kg(12_400)).toBe("12 kg");
    expect(distance(347)).toBe("350 m");
    expect(distance(2400)).toBe("2.4 km");
  });

  it("rounds minutes left up and never goes negative", () => {
    const now = Date.parse("2026-10-26T10:00:00Z");
    expect(minutesLeft("2026-10-26T10:02:10Z", now)).toBe(3);
    expect(minutesLeft("2026-10-26T09:59:00Z", now)).toBe(0);
  });
});

describe("fitWithin", () => {
  it("shrinks the long side to 1600 px and keeps the shape", () => {
    expect(fitWithin(4000, 3000)).toEqual([1600, 1200]);
    expect(fitWithin(3000, 4000)).toEqual([1200, 1600]);
  });

  it("never enlarges a small photo", () => {
    expect(fitWithin(800, 600)).toEqual([800, 600]);
  });
});
