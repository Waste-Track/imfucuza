import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { Alert, Button, Card, Field, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis, PLASTIC_LABELS } from "../../lib/format";
import { currentFix } from "../../lib/geo";
import type { Offering, PlasticType } from "../../lib/types";
import { useMe } from "../../session";

export function NewRequest() {
  const me = useMe();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const offering: Offering = params.get("type") === "plastic" ? "plastic" : "refuse";
  const [declared, setDeclared] = useState<PlasticType>("pet");
  const [where, setWhere] = useState<"home" | "here">("home");
  // One key per visit to this screen, so a retried tap never creates two pickups.
  const [key] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const location = where === "here" ? await currentFix() : undefined;
      const pickup = await api.requestPickup(
        {
          offering,
          declared_type: offering === "plastic" ? declared : undefined,
          lat: location?.lat,
          lng: location?.lng,
        },
        key,
      );
      navigate(`/pickups/${pickup.id}`, { replace: true });
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Request failed.");
      setBusy(false);
    }
  };

  return (
    <Screen
      title="New request"
      back="/"
      footer={
        <Button busy={busy} onClick={submit}>
          {offering === "refuse" ? "Request pickup" : "Request plastic collection"}
        </Button>
      }
    >
      <div className="segmented" role="radiogroup" aria-label="What to collect">
        {(["refuse", "plastic"] as const).map((o) => (
          <button
            key={o}
            type="button"
            role="radio"
            aria-checked={offering === o}
            onClick={() => setParams(o === "plastic" ? { type: "plastic" } : {}, { replace: true })}
          >
            {o === "refuse" ? "General waste" : "Plastic drop-off"}
          </button>
        ))}
      </div>

      {offering === "plastic" && (
        <Field id="declared" label="What kind of plastic?">
          <select
            id="declared"
            value={declared}
            onChange={(e) => setDeclared(e.target.value as PlasticType)}
          >
            {Object.entries(PLASTIC_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </Field>
      )}

      <fieldset className="field">
        <legend>Where</legend>
        <label className="check">
          <input type="radio" name="where" checked={where === "home"} onChange={() => setWhere("home")} />
          <span>My home address</span>
        </label>
        <label className="check">
          <input type="radio" name="where" checked={where === "here"} onChange={() => setWhere("here")} />
          <span>Where I am now (uses GPS)</span>
        </label>
      </fieldset>

      {offering === "refuse" ? (
        <Card tone="cream">
          <div className="row-between big">
            <strong>Flat fee</strong>
            <strong>{cedis(me.refuse_fee_pesewas)}</strong>
          </div>
          <p className="small">
            Next you approve a hold on your mobile money. You're charged only when you give the
            rider your PIN. If the pickup doesn't happen, the hold is released.
          </p>
        </Card>
      ) : (
        <Card tone="cream">
          <p className="small">
            Bag your sorted plastic. The rider weighs it at your door and a photo is checked. Points
            are added once you give the rider your PIN.
          </p>
        </Card>
      )}

      {error && <Alert>{error}</Alert>}
    </Screen>
  );
}
