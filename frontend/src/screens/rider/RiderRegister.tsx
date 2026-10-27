import { useState } from "react";
import { Alert, Button, Field, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { ghanaPhone } from "../../lib/format";
import { useSession } from "../../session";

/** The rider application from the design (R1). A supervisor approves it. */
export function RiderRegister({ onBack }: { onBack: () => void }) {
  const { me, session, refreshMe } = useSession();
  const signedInPhone = session?.user.phone ? `+${session.user.phone.replace(/^\+/, "")}` : "";
  const [name, setName] = useState("");
  const [payoutPhone, setPayoutPhone] = useState(signedInPhone);
  const [plate, setPlate] = useState("");
  const [area, setArea] = useState("Madina");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!me) return;
    const payout = ghanaPhone(payoutPhone);
    if (!payout) {
      setError("Enter a Ghanaian mobile money number, like 024 123 4567.");
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await api.applyAsRider({
        consent_version: me.consent_version,
        name: name.trim(),
        payout_phone: payout,
        tricycle_plate: plate.trim(),
        work_area: area.trim(),
      });
      await refreshMe();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't send your application. Try again.");
      setBusy(false);
    }
  };

  const ready = name.trim().length >= 2 && plate.trim().length >= 2 && area.trim().length >= 2;

  return (
    <Screen
      title="Register your tricycle"
      eyebrow="RIDER"
      actions={
        <Button variant="quiet" onClick={onBack}>
          Back
        </Button>
      }
    >
      <form className="stack" onSubmit={submit}>
        <Field id="rname" label="Full name">
          <input id="rname" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="rphone" label="Mobile money phone number" hint="Your earnings are sent here.">
          <input
            id="rphone"
            type="tel"
            inputMode="tel"
            value={payoutPhone}
            onChange={(e) => setPayoutPhone(e.target.value)}
            aria-describedby="rphone-hint"
          />
        </Field>
        <Field id="rplate" label="Tricycle registration number">
          <input
            id="rplate"
            autoCapitalize="characters"
            placeholder="M-26-GR 1234"
            value={plate}
            onChange={(e) => setPlate(e.target.value)}
          />
        </Field>
        <Field id="rarea" label="Where you work">
          <input id="rarea" value={area} onChange={(e) => setArea(e.target.value)} />
        </Field>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            I agree that Imfucuza stores my name, number, tricycle number and, while I'm online, my
            phone's location, so dispatch can find the nearest rider. Households I'm collecting from
            can see my first name and call me. My data is handled under the Data Protection Act,
            2012 (Act 843). Notice version {me?.consent_version}.
          </span>
        </label>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" busy={busy} disabled={!consent || !ready}>
          Submit for approval
        </Button>
        <p className="hint">
          A supervisor checks your details and tricycle before you get jobs. No smartphone? Ask the
          Imfucuza supervisor to register you for the USSD menu.
        </p>
      </form>
    </Screen>
  );
}
