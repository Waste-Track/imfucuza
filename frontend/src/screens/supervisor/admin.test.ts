import { describe, expect, it, vi } from "vitest";
import { ago } from "./admin";

vi.mock("../../lib/supabase", () => ({ supabase: {} }));

describe("ago", () => {
  const now = Date.parse("2026-10-27T12:00:00Z");

  it("reads like a person would say it", () => {
    expect(ago("2026-10-27T11:59:40Z", now)).toBe("just now");
    expect(ago("2026-10-27T11:35:00Z", now)).toBe("25 min ago");
    expect(ago("2026-10-27T07:00:00Z", now)).toBe("5 h ago");
    expect(ago("2026-10-23T12:00:00Z", now)).toBe("4 days ago");
  });
});
