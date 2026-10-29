import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadIntent } from "../lib/intent";
import { Landing } from "./Landing";

vi.mock("../lib/supabase", () => ({ supabase: { auth: {} } }));

const show = () =>
  render(
    <MemoryRouter>
      <Landing />
    </MemoryRouter>,
  );

describe("Landing", () => {
  beforeEach(() => localStorage.clear());

  it("opens on what the service is, not a sign-in form", () => {
    show();

    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/Waste collected/);
    expect(screen.queryByLabelText("Mobile number")).not.toBeInTheDocument();
  });

  it("confirms the number as step one of registering, and remembers the choice", async () => {
    show();

    await userEvent.click(screen.getByRole("button", { name: /Register as a rider/ }));

    expect(screen.getByRole("heading", { name: "Confirm your number" })).toBeInTheDocument();
    expect(screen.getByText("RIDER · STEP 1 OF 2")).toBeInTheDocument();
    expect(loadIntent()).toBe("rider");
  });

  it("lets a registered user sign in, and forgets any earlier choice", async () => {
    show();
    await userEvent.click(screen.getByRole("button", { name: /Get my waste collected/ }));
    await userEvent.click(screen.getByRole("button", { name: "Back" }));

    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(loadIntent()).toBeUndefined();
  });
});
