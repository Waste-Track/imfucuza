import { useState } from "react";
import { useParams } from "react-router";
import { Icon } from "../../components/Icon";
import { PhotoCapture } from "../../components/PhotoCapture";
import { PinPad } from "../../components/PinPad";
import { Alert, Button, ButtonLink, Card, Field, Loading, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis, kg, PLASTIC_LABELS } from "../../lib/format";
import { currentFix, directionsUrl } from "../../lib/geo";
import type { JobView } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";
import { jobHeadline } from "./jobStatus";

export function RiderJob() {
  const { id = "" } = useParams();
  const [finished, setFinished] = useState(false);
  const { data: jobs, error, reload } = usePoll(api.jobs, 5_000);
  const job = jobs?.find((j) => j.pickup_id === id);

  if (finished || (jobs && !job)) {
    return (
      <Screen title="Job" back="/">
        <Card tone="green">
          <strong className="big">{finished ? "Pickup complete" : "This job has ended"}</strong>
          <p>
            {finished
              ? "The household confirmed. Your earnings are recorded."
              : "It was completed, cancelled or moved to a supervisor."}
          </p>
        </Card>
        <ButtonLink to="/">Back to jobs</ButtonLink>
      </Screen>
    );
  }

  if (!job) {
    return (
      <Screen title="Job" back="/">
        {error ? <Alert>{error.message}</Alert> : <Loading />}
      </Screen>
    );
  }

  return (
    <Screen title={jobHeadline(job)} back="/">
      {error && <Alert tone="warning">Offline. Showing the last update.</Alert>}
      <JobCard job={job} />
      <Step job={job} onChange={reload} onFinished={() => setFinished(true)} />
    </Screen>
  );
}

function JobCard({ job }: { job: JobView }) {
  return (
    <Card>
      <dl className="facts">
        <div>
          <dt>Address</dt>
          <dd className="strong">{job.address_text ?? "No address given. Use the map pin."}</dd>
        </div>
        <div>
          <dt>Pickup</dt>
          <dd>
            {job.offering === "plastic"
              ? `Plastic${job.declared_type ? `: ${PLASTIC_LABELS[job.declared_type]}` : ""}`
              : "General waste"}
          </dd>
        </div>
        {job.earning_pesewas != null && (
          <div>
            <dt>You earn</dt>
            <dd className="earning">{cedis(job.earning_pesewas)}</dd>
          </div>
        )}
      </dl>
      <div className="stack-row">
        {job.household_phone && (
          <a className="button button-secondary" href={`tel:${job.household_phone}`}>
            <Icon name="phone" />
            Call
          </a>
        )}
        <a
          className="button button-secondary"
          href={directionsUrl(job.lat, job.lng)}
          target="_blank"
          rel="noreferrer"
        >
          <Icon name="map" />
          Directions
        </a>
      </div>
    </Card>
  );
}

function Step({
  job,
  onChange,
  onFinished,
}: {
  job: JobView;
  onChange: () => void;
  onFinished: () => void;
}) {
  switch (job.status) {
    case "assigned":
      return <Arrive pickupId={job.pickup_id} onDone={onChange} />;
    case "location_issue":
      return (
        <Alert tone="warning">
          Call the household to find the right place. A supervisor is following up.
        </Alert>
      );
    case "arrived":
      return job.offering === "refuse" ? (
        <Collected pickupId={job.pickup_id} onDone={onChange} />
      ) : (
        <WeighAndPhoto job={job} onDone={onChange} />
      );
    case "verifying":
      return <Loading label="Checking the photo. This takes a few seconds." />;
    case "rider_review":
      return <RiderReview job={job} onDone={onChange} />;
    case "awaiting_pin":
      return (
        <Card>
          <PinPad
            label="Ask the household for the 6 digit PIN from their SMS and enter it."
            confirmText="Confirm pickup"
            submit={async (pin) => {
              await api.riderPin(job.pickup_id, pin);
              onFinished();
            }}
          />
          <p className="hint">
            No SMS? The household can ask for a new PIN in their app or by dialling the USSD code.
          </p>
        </Card>
      );
    default:
      return null;
  }
}

function useAction(onDone: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(undefined);
    try {
      await action();
      onDone();
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "That didn't work.");
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run };
}

function Arrive({ pickupId, onDone }: { pickupId: string; onDone: () => void }) {
  const { busy, error, run } = useAction(onDone);
  return (
    <div className="stack">
      <p className="muted">Call the household when you're close if you need directions.</p>
      {error && <Alert>{error}</Alert>}
      <Button busy={busy} onClick={() => run(async () => api.arrive(pickupId, await currentFix()))}>
        I've arrived
      </Button>
    </div>
  );
}

function Collected({ pickupId, onDone }: { pickupId: string; onDone: () => void }) {
  const { busy, error, run } = useAction(onDone);
  return (
    <div className="stack">
      <Card tone="green">
        Put the bag on your tricycle first. Then the household gets a PIN by SMS to give you.
      </Card>
      {error && <Alert>{error}</Alert>}
      <Button busy={busy} onClick={() => run(() => api.collected(pickupId))}>
        Waste collected
      </Button>
    </div>
  );
}

function WeighAndPhoto({ job, onDone }: { job: JobView; onDone: () => void }) {
  const [weightKg, setWeightKg] = useState(job.weight_g ? String(job.weight_g / 1000) : "");
  const grams = Math.round(Number(weightKg.replace(",", ".")) * 1000);
  const valid = Number.isFinite(grams) && grams > 0;

  return (
    <div className="stack">
      {job.weight_g != null && (
        <Alert tone="warning">The last photo couldn't be used. Take another one in good light.</Alert>
      )}
      <Field id="weight" label="Weight on the scale (kg)">
        <input
          id="weight"
          inputMode="decimal"
          placeholder="3.5"
          value={weightKg}
          onChange={(e) => setWeightKg(e.target.value)}
        />
      </Field>
      <PhotoCapture
        instructions="Photograph the whole bag of plastic, with its printed code in the frame."
        sendText={valid ? `Send ${kg(grams)} and photo` : "Enter the weight first"}
        disabled={!valid}
        send={async (photo) => {
          await api.submitPlastic(job.pickup_id, grams, photo);
          onDone();
        }}
      />
    </div>
  );
}

function RiderReview({ job, onDone }: { job: JobView; onDone: () => void }) {
  const { busy, error, run } = useAction(onDone);
  const type = job.declared_type ? PLASTIC_LABELS[job.declared_type] : "plastic";
  return (
    <div className="stack">
      <Card tone="amber">
        <strong className="big">The photo check wasn't sure</strong>
        <p>
          Look inside the bag. Accept only if it is clean, sorted {type.toLowerCase()}. Your decision
          is recorded with the photo and may be checked by a supervisor.
        </p>
      </Card>
      {error && <Alert>{error}</Alert>}
      <div className="stack-row">
        <Button variant="danger" busy={busy} onClick={() => run(() => api.riderDecision(job.pickup_id, false))}>
          Reject
        </Button>
        <Button busy={busy} className="grow2" onClick={() => run(() => api.riderDecision(job.pickup_id, true))}>
          Accept plastic
        </Button>
      </div>
    </div>
  );
}
