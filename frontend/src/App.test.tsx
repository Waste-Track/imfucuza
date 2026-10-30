import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { Me } from "./lib/types";

vi.mock("./lib/supabase", () => ({ supabase: { auth: {} } }));
vi.mock("./screens/supervisor/Console", () => ({ Console: () => <h1>Console</h1> }));

const session = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock("./session", () => ({ useSession: () => session.current }));

const supervisor = { role: "supervisor", status: "active" } as Me;

describe("App", () => {
  beforeEach(() => {
    session.current = {
      session: { user: { id: "u1" } },
      me: supervisor,
      choseOther: "household",
      acknowledge: vi.fn(),
      signOut: vi.fn(),
    };
  });

  it("says why signing up opened another account, instead of opening it silently", async () => {
    render(<App />, { wrapper: MemoryRouter });

    expect(screen.getByRole("heading", { name: "This number is already registered" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Console" })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Use a different number" }));
    expect(session.current.signOut).toHaveBeenCalled();
  });

  it("opens the account once they carry on", () => {
    session.current.choseOther = undefined;
    render(<App />, { wrapper: MemoryRouter });

    expect(screen.getByRole("heading", { name: "Console" })).toBeInTheDocument();
  });
});
