import { useState } from "react";
import { ApiError } from "../lib/api";
import { Alert, Button } from "./ui";

export const PIN_LENGTH = 6;

/**
 * Enter the household's 6 digit PIN. `submit` resolves when the pickup is
 * complete; a wrong PIN comes back as a 422 with `tries_left`.
 */
export function PinPad({
  submit,
  label,
  confirmText,
}: {
  submit: (pin: string) => Promise<unknown>;
  label: string;
  confirmText: string;
}) {
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const press = (digit: string) => setPin((p) => (p.length < PIN_LENGTH ? p + digit : p));

  const confirm = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await submit(pin);
    } catch (e) {
      setPin("");
      setError(pinError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pin-pad">
      <p className="lead">{label}</p>
      <input
        className="pin-input"
        inputMode="numeric"
        autoComplete="one-time-code"
        pattern="[0-9]*"
        maxLength={PIN_LENGTH}
        aria-label="PIN"
        value={pin}
        onChange={(e) => setPin(e.target.value.replace(/\D/g, "").slice(0, PIN_LENGTH))}
      />
      <div className="pin-boxes" aria-hidden="true">
        {Array.from({ length: PIN_LENGTH }, (_, i) => (
          <div key={i} className={i < pin.length ? "pin-box filled" : "pin-box"}>
            {pin[i] ?? ""}
          </div>
        ))}
      </div>
      {error && <Alert>{error}</Alert>}
      <div className="keypad">
        {["1", "2", "3", "4", "5", "6", "7", "8", "9"].map((d) => (
          <button key={d} type="button" className="key" onClick={() => press(d)}>
            {d}
          </button>
        ))}
        <span />
        <button type="button" className="key" onClick={() => press("0")}>
          0
        </button>
        <button type="button" className="key key-small" onClick={() => setPin((p) => p.slice(0, -1))}>
          Delete
        </button>
      </div>
      <Button busy={busy} disabled={pin.length !== PIN_LENGTH} onClick={confirm}>
        {confirmText}
      </Button>
    </div>
  );
}

export function pinError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 422 && error.body.detail === "wrong PIN") {
      if (error.body.locked) return "Too many wrong tries. The PIN is locked; ask for a new one.";
      const left = Number(error.body.tries_left);
      return `Wrong PIN. ${left} ${left === 1 ? "try" : "tries"} left.`;
    }
    return error.message;
  }
  return "Something went wrong. Try again.";
}
