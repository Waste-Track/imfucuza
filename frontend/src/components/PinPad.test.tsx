import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../lib/api";
import { PinPad, pinError } from "./PinPad";

vi.mock("../lib/supabase", () => ({ supabase: {} }));

describe("PinPad", () => {
  it("enables confirm only once six digits are in, then sends them", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<PinPad label="Enter PIN" confirmText="Confirm" submit={submit} />);
    const confirm = screen.getByRole("button", { name: "Confirm" });

    for (const digit of ["4", "7", "2", "9", "1"]) {
      await userEvent.click(screen.getByRole("button", { name: digit }));
    }
    expect(confirm).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "8" }));
    expect(confirm).toBeEnabled();

    await userEvent.click(confirm);
    expect(submit).toHaveBeenCalledWith("472918");
  });

  it("shows tries left after a wrong PIN and clears the entry", async () => {
    const submit = vi
      .fn()
      .mockRejectedValue(new ApiError(422, { detail: "wrong PIN", tries_left: 2, locked: false }));
    render(<PinPad label="Enter PIN" confirmText="Confirm" submit={submit} />);

    await userEvent.type(screen.getByLabelText("PIN"), "123456");
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Wrong PIN. 2 tries left.");
    expect(screen.getByLabelText("PIN")).toHaveValue("");
  });
});

describe("pinError", () => {
  it("explains a locked PIN", () => {
    expect(pinError(new ApiError(422, { detail: "wrong PIN", tries_left: 0, locked: true }))).toMatch(
      /locked/,
    );
  });

  it("passes other Engine messages through", () => {
    expect(pinError(new ApiError(409, { detail: "no live PIN" }))).toBe("No live PIN");
  });
});
