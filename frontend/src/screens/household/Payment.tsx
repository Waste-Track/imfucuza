import { useState } from "react";
import { Alert, Button, Card, Field } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis } from "../../lib/format";
import type { Network, PaymentStarted, PickupView } from "../../lib/types";

const NETWORKS: { id: Network; label: string }[] = [
  { id: "mtn", label: "MTN MoMo" },
  { id: "telecel", label: "Telecel Cash" },
  { id: "airteltigo", label: "AirtelTigo Money" },
];

/** Shown while a refuse pickup is awaiting payment. The pickup moves on by
 * itself once the provider confirms, so the parent screen keeps polling. */
export function Payment({ pickup, onChange }: { pickup: PickupView; onChange: () => void }) {
  const [network, setNetwork] = useState<Network>("mtn");
  const [started, setStarted] = useState<PaymentStarted>();
  const [otp, setOtp] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const run = async (action: () => Promise<PaymentStarted>) => {
    setBusy(true);
    setError(undefined);
    try {
      const next = await action();
      setStarted(next);
      if (next.state === "failed") setError("The payment didn't go through. Try again.");
      onChange();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Payment failed. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (started && started.state === "pending" && !started.needs_otp) {
    return (
      <Card tone="cream">
        <strong className="big">Approve on your phone</strong>
        <p>
          {started.display_text ??
            "A mobile money prompt is on its way. Enter your MoMo PIN on your phone to approve the hold."}
        </p>
        <p className="small">This screen updates by itself once the payment goes through.</p>
        <Button variant="quiet" onClick={() => setStarted(undefined)}>
          No prompt? Try again
        </Button>
      </Card>
    );
  }

  if (started?.needs_otp) {
    return (
      <Card>
        <p>{started.display_text ?? "Enter the code your network sent you."}</p>
        <Field id="otp" label="Code">
          <input
            id="otp"
            inputMode="numeric"
            autoComplete="one-time-code"
            value={otp}
            onChange={(e) => setOtp(e.target.value.trim())}
          />
        </Field>
        {error && <Alert>{error}</Alert>}
        <Button
          busy={busy}
          disabled={otp.length < 3}
          onClick={() => run(() => api.payOtp(pickup.id, started.reference, otp))}
        >
          Send code
        </Button>
      </Card>
    );
  }

  return (
    <Card>
      <strong className="big">Pay {cedis(pickup.fee_pesewas ?? 0)} with mobile money</strong>
      <p className="small">
        The money is held, not taken. You're charged only when you give the rider your PIN.
      </p>
      <fieldset className="field">
        <legend>Your network</legend>
        {NETWORKS.map((n) => (
          <label key={n.id} className="check">
            <input
              type="radio"
              name="network"
              checked={network === n.id}
              onChange={() => setNetwork(n.id)}
            />
            <span>{n.label}</span>
          </label>
        ))}
      </fieldset>
      <p className="hint">The prompt goes to the number you signed in with.</p>
      {error && <Alert>{error}</Alert>}
      <Button busy={busy} onClick={() => run(() => api.pay(pickup.id, network))}>
        Approve hold
      </Button>
    </Card>
  );
}
