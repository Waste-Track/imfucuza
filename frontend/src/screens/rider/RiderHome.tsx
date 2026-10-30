import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router";
import { Icon } from "../../components/Icon";
import { RiderMap } from "../../components/RiderMap";
import { Alert, Button, Card, RiderNav, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis, distance, minutesLeft } from "../../lib/format";
import type { OfferView } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { useDuty } from "./duty";
import { jobHeadline } from "./jobStatus";

export function RiderHome() {
  const me = useMe();
  const duty = useDuty();
  const jobs = usePoll(api.jobs, 10_000);
  const offers = usePoll(
    () => (duty.onDuty ? api.offers() : Promise.resolve([])),
    duty.onDuty ? 5_000 : 0,
    [duty.onDuty],
  );
  const earnings = usePoll(api.earnings, 60_000);
  const current = jobs.data ?? [];
  const pins = current.map((job) => ({ lat: job.lat, lng: job.lng, label: jobHeadline(job) }));

  return (
    <Screen
      title="Jobs"
      eyebrow={me.name ? `RIDER · ${me.name.toUpperCase()}` : "RIDER"}
      actions={<DutyPill />}
      nav={<RiderNav />}
    >
      {duty.error && <Alert>{duty.error}</Alert>}
      {duty.problem && <Alert tone="warning">{duty.problem}</Alert>}

      <RiderMap me={duty.position} pins={pins} />

      <div className="today" aria-label="Today">
        <Stat label="Jobs today" value={earnings.data ? String(earnings.data.jobs_today) : "…"} />
        <Stat label="Earned today" value={earnings.data ? cedis(earnings.data.today_pesewas) : "…"} />
        <Stat label="Owed to you" value={earnings.data ? cedis(earnings.data.owed_pesewas) : "…"} />
      </div>

      {current.map((job) => (
        <Link key={job.pickup_id} to={`/jobs/${job.pickup_id}`} className="card card-green tap">
          <div className="eyebrow">CURRENT JOB</div>
          <div className="row-between">
            <strong className="big">{jobHeadline(job)}</strong>
            <Icon name="forward" />
          </div>
          <div className="small">{job.address_text ?? "See the map for the location"}</div>
        </Link>
      ))}

      {duty.onDuty && offers.data?.map((offer) => <Offer key={offer.id} offer={offer} onGone={offers.reload} />)}

      {duty.onDuty && offers.data?.length === 0 && !current.length && (
        <Card>
          <strong>Waiting for pickups</strong>
          <p className="muted small">
            New pickups near you appear here, and you have a few minutes to accept each one. Keep the app open
            so we know where you are.
          </p>
        </Card>
      )}

      {!duty.onDuty && !duty.switching && (
        <Card tone="cream">
          <strong className="big">You're on a break</strong>
          <p className="small">You won't get new pickups until you're back online.</p>
          <Button onClick={() => void duty.setOnDuty(true)}>Go back online</Button>
        </Card>
      )}

      {duty.onDuty && (
        <Button variant="quiet" busy={duty.switching} onClick={() => void duty.setOnDuty(false)}>
          Take a break
        </Button>
      )}
    </Screen>
  );
}

function DutyPill() {
  const { onDuty, switching } = useDuty();
  const text = onDuty ? "Online" : switching ? "Going online…" : "On a break";
  return (
    <span className={onDuty ? "status-pill status-pill-on" : "status-pill"} role="status">
      <span className="status-dot" aria-hidden="true" />
      {text}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
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
