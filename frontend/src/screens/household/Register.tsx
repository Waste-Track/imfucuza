import { useState } from "react";
import { Alert, Button, Field, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { currentFix } from "../../lib/geo";
import { useSession } from "../../session";

export function Register() {
  const { me, refreshMe, signOut } = useSession();
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [location, setLocation] = useState<{ lat: number; lng: number }>();
  const [consent, setConsent] = useState(false);
  const [locating, setLocating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const locate = async () => {
    setLocating(true);
    setError(undefined);
    try {
      const fix = await currentFix();
      setLocation({ lat: fix.lat, lng: fix.lng });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLocating(false);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!me) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.register({
        consent_version: me.consent_version,
        name: name.trim() || undefined,
        address_text: address.trim() || undefined,
        ...location,
      });
      await refreshMe();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Registration failed. Try again.");
      setBusy(false);
    }
  };

  return (
    <Screen
      title="Set up your household"
      eyebrow="WELCOME"
      actions={
        <Button variant="quiet" onClick={signOut}>
          Sign out
        </Button>
      }
    >
      <form className="stack" onSubmit={submit}>
        <Field id="name" label="Your name (optional)">
          <input id="name" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="address" label="House and street" hint="Something a rider can find: house number, landmark, gate colour.">
          <input
            id="address"
            autoComplete="street-address"
            placeholder="House 14, Madina Estates, blue gate"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            aria-describedby="address-hint"
          />
        </Field>
        <div className="card">
          <div className="row-between">
            <div>
              <strong>Home location</strong>
              <div className="hint">
                {location ? "Saved from your phone's GPS" : "Use your phone's GPS while you're at home"}
              </div>
            </div>
            <Button variant="secondary" busy={locating} onClick={locate}>
              {location ? "Update" : "Use GPS"}
            </Button>
          </div>
        </div>
        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span>
            I agree that Imfucuza stores my number, address and location to arrange pickups, and that
            the rider assigned to my pickup can see my number to call me. My data is handled under
            the Data Protection Act, 2012 (Act 843). Notice version {me?.consent_version}.
          </span>
        </label>
        {error && <Alert>{error}</Alert>}
        <Button type="submit" busy={busy} disabled={!consent}>
          Create my household
        </Button>
      </form>
    </Screen>
  );
}
