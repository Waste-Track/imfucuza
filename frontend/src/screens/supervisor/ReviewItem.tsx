import { useState } from "react";
import { Link, useParams } from "react-router";
import { Alert, Button, Card, Field, Loading } from "../../components/ui";
import { cedis, kg, PLASTIC_LABELS } from "../../lib/format";
import type { PlasticType } from "../../lib/types";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, REVIEW_LABELS, when, type ComplaintOutcome, type Photo, type ReviewDetail } from "./admin";
import { Page } from "./Console";

export function ReviewItem({ onChange }: { onChange: () => void }) {
  const { id = "" } = useParams();
  const me = useMe();
  const detail = usePoll(() => admin.reviewItem(id), 0, [id]);
  const refresh = () => {
    void detail.reload();
    onChange();
  };
  const claim = useAction(refresh);

  if (!detail.data) {
    return (
      <Page title="Review item">
        {detail.error ? <Alert>{detail.error.message}</Alert> : <Loading />}
      </Page>
    );
  }

  const { item, pickup } = detail.data;
  const mine = item.assigned_to === me.user_id;
  const open = item.status === "open";

  return (
    <Page
      title={REVIEW_LABELS[item.type] ?? item.type}
      intro={
        <>
          <Link to="/" className="link">
            Queue
          </Link>{" "}
          · opened {when(item.created_at)}
          {item.assigned_to && !mine && ` · claimed by ${item.assigned_to_name ?? "another supervisor"}`}
        </>
      }
      actions={
        open &&
        !mine && (
          <Button busy={claim.busy} onClick={() => claim.run(() => admin.claim(item.id))}>
            {item.assigned_to ? "Take over" : "Claim"}
          </Button>
        )
      }
    >
      {claim.error && <Alert>{claim.error}</Alert>}
      {!open && (
        <Alert tone="info">
          Resolved {when(item.resolved_at)}: {item.resolution}
        </Alert>
      )}

      <div className="grid-2">
        <div className="stack">
          <Card>
            <h2 className="card-title">What happened</h2>
            <p>{String(item.payload.reason ?? item.reason ?? "No reason recorded.")}</p>
            {typeof item.payload.details === "string" && (
              <blockquote className="quote">{item.payload.details}</blockquote>
            )}
          </Card>
          {pickup && <PickupCard detail={detail.data} />}
          <Photos detail={detail.data} />
          <Verification detail={detail.data} />
        </div>
        <div className="stack">
          {open && mine && pickup && <Actions detail={detail.data} onDone={refresh} />}
          {open && mine && <Resolve itemId={item.id} onDone={refresh} />}
          {open && !mine && (
            <Alert tone="warning">Claim this item to act on it.</Alert>
          )}
          <Timeline detail={detail.data} />
        </div>
      </div>
    </Page>
  );
}

function PickupCard({ detail }: { detail: ReviewDetail }) {
  const p = detail.pickup!;
  const pin = detail.pins?.at(-1);
  return (
    <Card>
      <h2 className="card-title">Pickup</h2>
      <dl className="facts grid-facts">
        <div>
          <dt>Type</dt>
          <dd>{p.offering === "plastic" ? "Plastic" : "General waste"}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd className="strong">{p.status.replace(/_/g, " ")}</dd>
        </div>
        {p.fee_pesewas != null && (
          <div>
            <dt>Fee</dt>
            <dd>{cedis(p.fee_pesewas)}</dd>
          </div>
        )}
        {p.declared_type && (
          <div>
            <dt>Declared</dt>
            <dd>{PLASTIC_LABELS[p.declared_type as PlasticType] ?? p.declared_type}</dd>
          </div>
        )}
        {p.weight_g != null && (
          <div>
            <dt>Weight</dt>
            <dd>{kg(p.weight_g)}</dd>
          </div>
        )}
        {p.points_due != null && (
          <div>
            <dt>Points due</dt>
            <dd>{p.points_due}</dd>
          </div>
        )}
        <div>
          <dt>Requested</dt>
          <dd>{when(p.requested_at)}</dd>
        </div>
        {pin && (
          <div>
            <dt>PIN</dt>
            <dd>
              {pin.status}, {pin.attempts} wrong {pin.attempts === 1 ? "try" : "tries"}
            </dd>
          </div>
        )}
      </dl>
      {!!detail.payments?.length && (
        <ul className="plain small">
          {detail.payments.map((pay) => (
            <li key={pay.reference}>
              {pay.direction} {cedis(pay.amount_pesewas)}: {pay.status} · {when(pay.created_at)}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Photos({ detail }: { detail: ReviewDetail }) {
  const photos = detail.photos ?? [];
  const copies = detail.possible_copies_of ?? [];
  if (!photos.length) return null;
  return (
    <Card>
      <h2 className="card-title">Photos</h2>
      <div className="photo-grid">
        {photos.map((photo) => (
          <PhotoTile key={photo.id} photo={photo} label={`${photo.source ?? "Photo"} · ${when(photo.created_at)}`} />
        ))}
      </div>
      {!!copies.length && (
        <>
          <h3 className="small strong">Looks like these earlier photos</h3>
          <div className="photo-grid">
            {copies.map((photo) => (
              <PhotoTile key={photo.id} photo={photo} label={`Another pickup · ${when(photo.created_at)}`} />
            ))}
          </div>
        </>
      )}
      <p className="hint">Links expire after a few minutes. Reload the page if a photo stops loading.</p>
    </Card>
  );
}

function PhotoTile({ photo, label }: { photo: Photo; label: string }) {
  return (
    <figure className="photo-tile">
      {photo.url ? (
        <a href={photo.url} target="_blank" rel="noreferrer">
          <img src={photo.url} alt={label} />
        </a>
      ) : (
        <div className="photo-missing">Storage unavailable</div>
      )}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

function Verification({ detail }: { detail: ReviewDetail }) {
  if (!detail.verification?.length) return null;
  return (
    <Card>
      <h2 className="card-title">Photo check</h2>
      <ul className="plain small">
        {detail.verification.map((v, i) => (
          <li key={i}>
            <strong>{v.stage}</strong>: {v.decision}
            {v.confidence != null && ` (${Math.round(v.confidence * 100)}%)`}
            {v.reason && `, ${v.reason}`}
            {v.is_override && " · rider override"} · {when(v.created_at)}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function Timeline({ detail }: { detail: ReviewDetail }) {
  if (!detail.timeline?.length) return null;
  return (
    <Card>
      <h2 className="card-title">Timeline</h2>
      <ol className="plain small events">
        {detail.timeline.map((e, i) => (
          <li key={i}>
            <span className="muted nowrap">{when(e.occurred_at)}</span> {e.name.replace(/[._]/g, " ")}{" "}
            <span className="muted">({e.actor_type})</span>
          </li>
        ))}
      </ol>
      {!!detail.messages?.length && (
        <>
          <h3 className="small strong">SMS sent</h3>
          <ul className="plain small">
            {detail.messages.map((m, i) => (
              <li key={i}>
                <span className="muted">{when(m.created_at)}</span> {m.body_masked}{" "}
                <span className="muted">({m.status})</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

const OUTCOMES: { value: ComplaintOutcome; label: string; hint: string }[] = [
  { value: "reissue_pin", label: "Send a new PIN", hint: "The pickup happened; let the household confirm it." },
  { value: "refund", label: "Refund", hint: "Give the household's money back." },
  { value: "grant_points", label: "Grant points", hint: "Plastic was wrongly rejected." },
  { value: "dismiss", label: "Dismiss", hint: "No change. Explain why in the note." },
];

function Actions({ detail, onDone }: { detail: ReviewDetail; onDone: () => void }) {
  const p = detail.pickup!;
  const action = useAction(onDone);
  const [note, setNote] = useState("");
  const [outcome, setOutcome] = useState<ComplaintOutcome>("reissue_pin");
  const [riderId, setRiderId] = useState("");
  const riders = usePoll(admin.riders, 0);
  const [done, setDone] = useState<string>();

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setDone(undefined);
    if (await action.run(fn)) setDone(label);
  };

  const noteOk = note.trim().length >= 3;
  const complaint = detail.item.type === "household_complaint" || p.status === "disputed";
  const material = p.offering === "plastic" && p.status === "awaiting_pin" && detail.item.type === "manual_verification";
  const canMove = ["pending_dispatch", "offered", "assigned"].includes(p.status);
  const canCancel = ["pending_dispatch", "offered", "assigned", "arrived", "location_issue", "awaiting_pin", "verifying", "rider_review", "awaiting_payment"].includes(p.status);

  return (
    <Card>
      <h2 className="card-title">Act on the pickup</h2>
      <Field id="note" label="Note (kept with the decision)">
        <textarea id="note" rows={3} maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>

      {material && (
        <div className="stack-row">
          <Button variant="danger" disabled={!noteOk} busy={action.busy} onClick={() => run("Plastic rejected.", () => admin.checkMaterial(p.id, false, note.trim()))}>
            Reject plastic
          </Button>
          <Button disabled={!noteOk} busy={action.busy} onClick={() => run("Plastic accepted.", () => admin.checkMaterial(p.id, true, note.trim()))}>
            Accept plastic
          </Button>
        </div>
      )}

      {complaint && (
        <fieldset className="field">
          <legend>Answer the complaint</legend>
          {OUTCOMES.map((o) => (
            <label key={o.value} className="check">
              <input type="radio" name="outcome" checked={outcome === o.value} onChange={() => setOutcome(o.value)} />
              <span>
                <strong>{o.label}</strong>
                <span className="block muted small">{o.hint}</span>
              </span>
            </label>
          ))}
          <Button
            disabled={!noteOk}
            busy={action.busy}
            onClick={() =>
              run("Decision recorded. Costly decisions past the daily limit wait in Approvals.", () =>
                admin.decideComplaint(p.id, outcome, note.trim()),
              )
            }
          >
            Decide
          </Button>
        </fieldset>
      )}

      <div className="button-row">
        {p.status === "awaiting_pin" && (
          <Button variant="secondary" busy={action.busy} onClick={() => run("New PIN sent.", () => admin.reissuePin(p.id))}>
            Send new PIN
          </Button>
        )}
        {canMove && (
          <Button variant="secondary" busy={action.busy} onClick={() => run("Looking for another rider.", () => admin.redispatch(p.id))}>
            Find another rider
          </Button>
        )}
        {canCancel && (
          <Button variant="danger" disabled={!noteOk} busy={action.busy} onClick={() => run("Cancelled.", () => admin.cancel(p.id, note.trim()))}>
            Cancel pickup
          </Button>
        )}
      </div>

      {canMove && (
        <div className="row">
          <label className="visually-hidden" htmlFor="assign">
            Rider
          </label>
          <select id="assign" className="select grow" value={riderId} onChange={(e) => setRiderId(e.target.value)}>
            <option value="">Assign to a rider…</option>
            {riders.data
              ?.filter((r) => r.status === "active")
              .map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name ?? r.phone_e164} {r.on_duty ? "· on duty" : "· off duty"}
                </option>
              ))}
          </select>
          <Button variant="secondary" disabled={!riderId} busy={action.busy} onClick={() => run("Assigned.", () => admin.assign(p.id, riderId))}>
            Assign
          </Button>
        </div>
      )}

      {action.error && <Alert>{action.error}</Alert>}
      {done && <Alert tone="info">{done}</Alert>}
    </Card>
  );
}

function Resolve({ itemId, onDone }: { itemId: string; onDone: () => void }) {
  const [text, setText] = useState("");
  const action = useAction(onDone);
  return (
    <Card>
      <h2 className="card-title">Close this item</h2>
      <Field id="resolution" label="What was done">
        <textarea id="resolution" rows={2} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} />
      </Field>
      {action.error && <Alert>{action.error}</Alert>}
      <Button variant="secondary" disabled={text.trim().length < 3} busy={action.busy} onClick={() => action.run(() => admin.resolve(itemId, text.trim()))}>
        Resolve
      </Button>
    </Card>
  );
}
