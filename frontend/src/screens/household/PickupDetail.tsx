import { useState } from "react";
import { useParams } from "react-router";
import { PinPad } from "../../components/PinPad";
import { Alert, Button, ButtonLink, Card, Field, Loading, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis, kg, PLASTIC_LABELS } from "../../lib/format";
import { headline, isEnded, outcomeNote, steps } from "../../lib/pickupStatus";
import type { PickupStatus, PickupView } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";
import { Payment } from "./Payment";

// Before the rider arrives the household can still cancel.
const CANCELLABLE: readonly PickupStatus[] = [
  "awaiting_payment",
  "pending_dispatch",
  "offered",
  "assigned",
];
const COMPLAINABLE: readonly PickupStatus[] = ["awaiting_pin", "completed", "rejected", "unconfirmed"];

export function PickupDetail() {
  const { id = "" } = useParams();
  const { data: pickup, error, reload } = usePoll(() => api.pickup(id), 5_000, [id]);

  if (!pickup) {
    return (
      <Screen title="Pickup" back="/">
        {error ? <Alert>{error.message}</Alert> : <Loading />}
      </Screen>
    );
  }

  const ended = isEnded(pickup.status);
  const note = outcomeNote(pickup.status, pickup.offering);

  return (
    <Screen title={pickup.offering === "plastic" ? "Plastic drop-off" : "Pickup status"} back="/">
      {error && <Alert tone="warning">Offline. Showing the last update.</Alert>}

      <Card>
        <strong className="big">{headline(pickup.status, pickup.offering)}</strong>
        {!["cancelled", "expired", "failed", "refunded", "rejected", "unconfirmed"].includes(
          pickup.status,
        ) && <Timeline pickup={pickup} />}
        {note && <p className="small">{note}</p>}
      </Card>

      {pickup.status === "awaiting_payment" && <Payment pickup={pickup} onChange={reload} />}

      {pickup.offering === "plastic" && <PlasticSummary pickup={pickup} />}

      {pickup.status === "assigned" && (
        <p className="muted">
          The rider has your number and will call if they need directions. Keep your phone near you.
        </p>
      )}

      {pickup.photo_wanted && (
        <Card tone="amber">
          <strong className="big">Photo needed</strong>
          <p>
            Your rider weighed {pickup.weight_g ? kg(pickup.weight_g) : "your plastic"} and has no
            camera. Take a photo of the bag so it can be checked.
          </p>
          <ButtonLink to={`/pickups/${pickup.id}/photo`}>Take photo</ButtonLink>
        </Card>
      )}

      {pickup.status === "awaiting_pin" && <PinStep pickup={pickup} onDone={reload} />}

      {!ended && CANCELLABLE.includes(pickup.status) && (
        <CancelButton pickupId={pickup.id} onDone={reload} />
      )}

      {COMPLAINABLE.includes(pickup.status) && (
        <Complaint pickupId={pickup.id} beforePin={pickup.status === "awaiting_pin"} onDone={reload} />
      )}
    </Screen>
  );
}

function Timeline({ pickup }: { pickup: PickupView }) {
  return (
    <ol className="timeline">
      {steps(pickup.status, pickup.offering).map((step) => (
        <li key={step.label} className={`step step-${step.state}`}>
          <span className="dot" aria-hidden="true" />
          <span>
            {step.label}
            {step.state === "current" && <span className="visually-hidden"> (now)</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

function PlasticSummary({ pickup }: { pickup: PickupView }) {
  return (
    <Card>
      <dl className="facts">
        <div>
          <dt>Declared</dt>
          <dd>{pickup.declared_type ? PLASTIC_LABELS[pickup.declared_type] : "Plastic"}</dd>
        </div>
        {pickup.weight_g != null && (
          <div>
            <dt>Weight</dt>
            <dd>{kg(pickup.weight_g)}</dd>
          </div>
        )}
        {pickup.points_due != null && (
          <div>
            <dt>Points</dt>
            <dd>{pickup.points_due}</dd>
          </div>
        )}
      </dl>
    </Card>
  );
}

function PinStep({ pickup, onDone }: { pickup: PickupView; onDone: () => void }) {
  const [selfConfirm, setSelfConfirm] = useState(false);
  const [resent, setResent] = useState<string>();

  const resend = async () => {
    try {
      await api.resendPin(pickup.id);
      setResent("A new PIN is on its way by SMS.");
    } catch (e) {
      setResent(e instanceof ApiError ? e.message : "Couldn't send a new PIN.");
    }
  };

  return (
    <Card tone="dark">
      <strong className="big">Your PIN is in your SMS</strong>
      <p>
        We texted you a 6 digit PIN. Read it to the rider only after your{" "}
        {pickup.offering === "plastic" ? "plastic" : "waste"} is on the tricycle.
        {pickup.offering === "refuse" && pickup.fee_pesewas
          ? ` Giving it releases the ${cedis(pickup.fee_pesewas)} payment.`
          : ""}
      </p>
      {selfConfirm ? (
        <PinPad
          label="Or enter the PIN here yourself."
          confirmText="Confirm pickup"
          submit={async (pin) => {
            await api.confirm(pickup.id, pin);
            onDone();
          }}
        />
      ) : (
        <Button variant="secondary" onClick={() => setSelfConfirm(true)}>
          Enter the PIN myself
        </Button>
      )}
      <Button variant="quiet" onClick={resend}>
        No SMS? Send a new PIN
      </Button>
      {resent && <p className="small">{resent}</p>}
    </Card>
  );
}

function CancelButton({ pickupId, onDone }: { pickupId: string; onDone: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const cancel = async () => {
    setBusy(true);
    try {
      await api.cancel(pickupId);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't cancel.");
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div className="stack">
      {error && <Alert>{error}</Alert>}
      {confirming ? (
        <div className="stack-row">
          <Button variant="secondary" onClick={() => setConfirming(false)}>
            Keep it
          </Button>
          <Button variant="danger" busy={busy} onClick={cancel}>
            Yes, cancel
          </Button>
        </div>
      ) : (
        <Button variant="quiet" onClick={() => setConfirming(true)}>
          Cancel this request
        </Button>
      )}
    </div>
  );
}

function Complaint({
  pickupId,
  beforePin,
  onDone,
}: {
  pickupId: string;
  beforePin: boolean;
  onDone: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string>();
  const [error, setError] = useState<string>();

  const send = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const response = await api.complain(pickupId, details.trim());
      setResult(
        response.status === "disputed"
          ? "Your payment is on hold until a supervisor decides. You'll get an SMS."
          : "A supervisor will look into it. You'll get an SMS.",
      );
      setOpen(false);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't send. Try again.");
    } finally {
      setBusy(false);
    }
  };

  if (result) return <Alert tone="info">{result}</Alert>;
  if (!open) {
    return (
      <Button variant="quiet" onClick={() => setOpen(true)}>
        {beforePin ? "Something wrong? Report it before giving the PIN" : "Disagree? File a complaint"}
      </Button>
    );
  }
  return (
    <Card>
      <Field id="complaint" label="What went wrong?">
        <textarea
          id="complaint"
          rows={4}
          maxLength={500}
          value={details}
          onChange={(e) => setDetails(e.target.value)}
        />
      </Field>
      {error && <Alert>{error}</Alert>}
      <div className="stack-row">
        <Button variant="secondary" onClick={() => setOpen(false)}>
          Back
        </Button>
        <Button busy={busy} disabled={details.trim().length < 3} onClick={send}>
          Send to a supervisor
        </Button>
      </div>
    </Card>
  );
}
