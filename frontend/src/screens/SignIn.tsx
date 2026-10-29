import { useState } from "react";
import { Alert, Button, Field, Screen } from "../components/ui";
import { ghanaPhone } from "../lib/format";
import type { Intent } from "../lib/intent";
import { supabase } from "../lib/supabase";

export function SignIn({ intent, onBack }: { intent?: Intent; onBack: () => void }) {
  const [phone, setPhone] = useState("");
  const [sentTo, setSentTo] = useState<string>();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const sendCode = async (event: React.FormEvent) => {
    event.preventDefault();
    const e164 = ghanaPhone(phone);
    if (!e164) {
      setError("Enter a Ghanaian mobile number, like 024 123 4567.");
      return;
    }
    setBusy(true);
    setError(undefined);
    const { error } = await supabase.auth.signInWithOtp({ phone: e164 });
    setBusy(false);
    if (error) setError(error.message);
    else setSentTo(e164);
  };

  const verify = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!sentTo) return;
    setBusy(true);
    setError(undefined);
    const { error } = await supabase.auth.verifyOtp({ phone: sentTo, token: code, type: "sms" });
    setBusy(false);
    // On success the session listener takes over and opens the app.
    if (error) setError("That code didn't work. Check the SMS and try again.");
  };

  return (
    <Screen
      title={intent ? "Confirm your number" : "Sign in"}
      eyebrow={intent === "rider" ? "RIDER · STEP 1 OF 2" : intent ? "STEP 1 OF 2" : "IMFUCUZA"}
      actions={
        <Button variant="quiet" onClick={onBack}>
          Back
        </Button>
      }
    >
      {!sentTo ? (
        <form className="stack" onSubmit={sendCode}>
          <p className="lead">
            {intent === "rider"
              ? "Your number is your rider account. Job offers and your earnings go to it. We'll text you a code to confirm it's yours."
              : intent
                ? "Your number is your account. Your pickup PIN and mobile money prompt go to it, and your rider calls it. We'll text you a code to confirm it's yours."
                : "Enter the number you registered with. We'll text you a code."}
          </p>
          <Field id="phone" label="Mobile number">
            <input
              id="phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="024 123 4567"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy}>
            Text me a code
          </Button>
          {!intent && (
            <p className="hint">Riders registered by a supervisor: use that number.</p>
          )}
        </form>
      ) : (
        <form className="stack" onSubmit={verify}>
          <p className="lead">Enter the code we sent to {sentTo}.</p>
          <Field id="code" label="Code from SMS">
            <input
              id="code"
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="[0-9]*"
              maxLength={8}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          {error && <Alert>{error}</Alert>}
          <Button type="submit" busy={busy} disabled={code.length < 6}>
            {intent ? "Confirm" : "Sign in"}
          </Button>
          <Button variant="quiet" onClick={() => setSentTo(undefined)}>
            Use a different number
          </Button>
        </form>
      )}
    </Screen>
  );
}
