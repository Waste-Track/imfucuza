import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { Me } from "../../lib/types";
import { RiderDutyProvider } from "./duty";
import { RiderHome } from "./RiderHome";

vi.mock("../../lib/supabase", () => ({ supabase: { auth: {} } }));
vi.mock("../../components/RiderMap", () => ({ RiderMap: () => <div>map</div> }));

const me = vi.hoisted(() => ({ current: {} as Partial<Me> }));
vi.mock("../../session", () => ({
  useMe: () => me.current,
  useSession: () => ({ refreshMe: vi.fn() }),
}));

const earnings = { owed_pesewas: 1400, today_pesewas: 700, week_pesewas: 1400, jobs_today: 1, payout_phone: null, payouts: [] };

function show() {
  render(
    <MemoryRouter>
      <RiderDutyProvider>
        <RiderHome />
      </RiderDutyProvider>
    </MemoryRouter>,
  );
}

describe("RiderHome", () => {
  beforeEach(() => {
    me.current = { role: "rider", status: "active", name: "Wofa Kwame", on_duty: false };
    sessionStorage.clear();
    vi.restoreAllMocks();
    vi.spyOn(api, "setDuty").mockImplementation(async (onDuty) => ({ on_duty: onDuty }));
    vi.spyOn(api, "jobs").mockResolvedValue([]);
    vi.spyOn(api, "offers").mockResolvedValue([]);
    vi.spyOn(api, "earnings").mockResolvedValue(earnings);
  });

  it("goes online on opening, with no button to press", async () => {
    show();

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Online"));
    expect(api.setDuty).toHaveBeenCalledWith(true);
    expect(screen.queryByRole("button", { name: /Go online/ })).not.toBeInTheDocument();
    expect(await screen.findByText("GH₵ 7.00")).toBeInTheDocument();
  });

  it("stays on a break the rider took, until they come back", async () => {
    show();
    await userEvent.click(await screen.findByRole("button", { name: "Take a break" }));
    expect(await screen.findByText("You're on a break")).toBeInTheDocument();

    vi.mocked(api.setDuty).mockClear();
    me.current = { ...me.current, on_duty: false };
    show();

    expect(api.setDuty).not.toHaveBeenCalled();
  });
});
