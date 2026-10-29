import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Icon } from "../../components/Icon";
import { Alert, Button, Card, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis, distance, minutesLeft } from "../../lib/format";
import type { OfferView } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";
import { useRiderLocation } from "../../lib/useRiderLocation";
import { useMe } from "../../session";
import { jobHeadline } from "./jobStatus";

export function RiderHome() {
  const me = useMe();
  const [onDuty, setOnDuty] = useState(Boolean(me.on_duty));
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string>();
  const { problem } = useRiderLocation(onDuty);
  const jobs = usePoll(api.jobs, 10_000);
  const offers = usePoll(() => (onDuty ? api.offers() : Promise.resolve([])), onDuty ? 5_000 : 0, [onDuty]);

  const toggle = async () => {
    setSwitching(true);
    setError(undefined);
    try {
      const next = await api.setDuty(!onDuty);
      setOnDuty(next.on_duty);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't change your status.");
    } finally {
      setSwitching(false);
    }
  };

  return (
    <Screen
      title="Jobs"
      eyebrow={me.name ? `RIDER · ${me.name.toUpperCase()}` : "RIDER"}
      actions={
        <Link to="/account" className="icon-button" aria-label="Account">
          <Icon name="person" />
        </Link>
      }
    >
      <div className={onDuty ? "duty duty-on" : "duty"}>
        <div>
          <strong>{onDuty ? "You are online" : "You are offline"}</strong>
          <div className="small">{onDuty ? "You'll get offers near you" : "Go online to get offers"}</div>
        </div>
        <Button variant={onDuty ? "secondary" : "primary"} busy={switching} onClick={toggle}>
          {onDuty ? "Go offline" : "Go online"}
        </Button>
      </div>
      {error && <Alert>{error}</Alert>}
      {problem && <Alert tone="warning">{problem}</Alert>}

      {jobs.data?.map((job) => (
        <Link key={job.pickup_id} to={`/jobs/${job.pickup_id}`} className="card card-green tap">
          <div className="eyebrow">CURRENT JOB</div>
          <div className="row-between">
            <strong className="big">{jobHeadline(job)}</strong>
            <Icon name="forward" />
          </div>
          <div className="small">{job.address_text ?? "See the map for the location"}</div>
        </Link>
      ))}

      {onDuty && offers.data?.map((offer) => <Offer key={offer.id} offer={offer} onGone={offers.reload} />)}

      {onDuty && offers.data?.length === 0 && !jobs.data?.length && (
        <p className="muted center">No offers right now. Keep this screen open.</p>
      )}
    </Screen>
  );
}

function Offer({ offer, onGone }: { offer: OfferView; onGone: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<"accept" | "decline">();
  const [error, setError] = useState<string>();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(timer);
  }, []);

  const respond = async (accept: boolean) => {
    setBusy(accept ? "accept" : "decline");
    setError(undefined);
    try {
      if (accept) {
        const result = await api.accept(offer.id);
        navigate(`/jobs/${result.pickup_id}`);
      } else {
        await api.decline(offer.id);
        onGone();
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "That didn't work.");
      onGone();
    } finally {
      setBusy(undefined);
    }
  };

  const left = minutesLeft(offer.expires_at, now);
  return (
    <Card>
      <div className="row-between">
        <strong className="big">New pickup</strong>
        <span className="chip chip-cream">{left <= 1 ? "Reply now" : `Reply in ${left} min`}</span>
      </div>
      <dl className="facts">
        <div>
          <dt>Type</dt>
          <dd>{offer.offering === "plastic" ? "Plastic, paid per kg" : "General waste, flat fee"}</dd>
        </div>
        <div>
          <dt>Distance</dt>
          <dd>{distance(offer.distance_m)}</dd>
        </div>
        <div>
          <dt>You earn</dt>
          <dd className="earning">
            {offer.earning_pesewas != null
              ? cedis(offer.earning_pesewas)
              : offer.pay_per_kg_pesewas != null
                ? `${cedis(offer.pay_per_kg_pesewas)} per kg`
                : "–"}
          </dd>
        </div>
      </dl>
      {error && <Alert>{error}</Alert>}
      <div className="stack-row">
        <Button variant="danger" busy={busy === "decline"} disabled={!!busy} onClick={() => respond(false)}>
          Decline
        </Button>
        <Button className="grow2" busy={busy === "accept"} disabled={!!busy} onClick={() => respond(true)}>
          Accept pickup
        </Button>
      </div>
    </Card>
  );
}
