import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../lib/api";
import type { PickupView } from "../../lib/types";
import { Payment } from "./Payment";

vi.mock("../../lib/supabase", () => ({ supabase: { auth: {} } }));

const popup = vi.hoisted(() => ({ resumeTransaction: vi.fn() }));
vi.mock("@paystack/inline-js", () => ({
  default: class {
    resumeTransaction = popup.resumeTransaction;
  },
}));

const pickup = { id: "p1", fee_pesewas: 1000 } as PickupView;

describe("Payment", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    popup.resumeTransaction.mockReset();
    vi.spyOn(api, "checkout").mockResolvedValue({ reference: "imf_1", access_code: "ac_1" });
    vi.spyOn(api, "checkPayment").mockResolvedValue({ status: "pending_dispatch" });
  });

  it("opens Paystack's window for the transaction the Engine set up", async () => {
    render(<Payment pickup={pickup} onChange={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Pay GH₵ 10.00" }));

    expect(api.checkout).toHaveBeenCalledWith("p1");
    expect(popup.resumeTransaction).toHaveBeenCalledWith("ac_1", expect.any(Object));
  });

  it("confirms the payment as soon as the window says it went through", async () => {
    const onChange = vi.fn();
    render(<Payment pickup={pickup} onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Pay GH₵ 10.00" }));

    popup.resumeTransaction.mock.calls[0][1].onSuccess({ id: 1, reference: "imf_1", message: "ok" });

    expect(await screen.findByText("Confirming your payment")).toBeInTheDocument();
    expect(api.checkPayment).toHaveBeenCalledWith("p1", "imf_1");
    await vi.waitFor(() => expect(onChange).toHaveBeenCalled());
  });

  it("lets them try again after closing the window", async () => {
    render(<Payment pickup={pickup} onChange={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Pay GH₵ 10.00" }));

    popup.resumeTransaction.mock.calls[0][1].onCancel();

    expect(await screen.findByRole("button", { name: "Pay GH₵ 10.00" })).toBeEnabled();
  });
});
