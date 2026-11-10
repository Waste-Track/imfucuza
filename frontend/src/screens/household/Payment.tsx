import { useState } from "react";
import { Alert, Button, Card } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis } from "../../lib/format";
import type { PickupView } from "../../lib/types";

/** Shown while a refuse pickup is awaiting payment. Paystack's checkout window
 * asks for the network and number. The pickup moves on once the payment is
 * confirmed, so the parent screen keeps polling. */
export function Payment({ pickup, onChange }: { pickup: PickupView; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string>();

  const pay = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const { reference, access_code } = await api.checkout(pickup.id);
      // Loaded on first use: most screens never need it.
      const { default: PaystackPop } = await import("@paystack/inline-js");
      new PaystackPop().resumeTransaction(access_code, {
        onSuccess: () => {
          setConfirming(true);
          // The webhook confirms it too. This just saves waiting for it.
          api
            .checkPayment(pickup.id, reference)
            .catch(() => undefined)
            .finally(onChange);
        },
        onCancel: () => setBusy(false),
        onError: (e) => {
          setError(e.message || "The payment window didn't open. Try again.");
          setBusy(false);
        },
      });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The payment window didn't open. Try again.");
      setBusy(false);
    }
  };

  if (confirming) {
    return (
      <Card tone="cream">
        <strong className="big">Confirming your payment</strong>
        <p className="small">This updates by itself in a moment.</p>
      </Card>
    );
  }

  return (
    <Card>
      <strong className="big">Pay {cedis(pickup.fee_pesewas ?? 0)} with mobile money</strong>
      <p className="small">
        The money is held, not taken. You're charged only when you give the rider your PIN.
      </p>
      <p className="hint">Paystack opens to take your mobile money number and network.</p>
      {error && <Alert>{error}</Alert>}
      <Button busy={busy} onClick={pay}>
        Pay {cedis(pickup.fee_pesewas ?? 0)}
      </Button>
    </Card>
  );
}
