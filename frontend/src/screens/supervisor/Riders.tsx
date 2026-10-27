import { useState } from "react";
import { Alert, Button, Card, Field, Loading } from "../../components/ui";
import { cedis, ghanaPhone } from "../../lib/format";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, ago, type RiderRow } from "./admin";
import { Page, Tabs } from "./Console";

type Filter = "pending" | "active" | "suspended";

export function Riders({ onChange }: { onChange: () => void }) {
  const riders = usePoll(admin.riders, 30_000);
  const [filter, setFilter] = useState<Filter>("pending");
  const [open, setOpen] = useState<string>();
  const [adding, setAdding] = useState(false);
  const refresh = () => {
    void riders.reload();
    onChange();
  };

  const count = (f: Filter) => riders.data?.filter((r) => r.status === f).length ?? 0;
  const rows = riders.data?.filter((r) => r.status === filter) ?? [];

  return (
    <Page
      title="Riders"
      intro="Approve applications after checking the rider and tricycle in person."
      actions={
        <Button variant="secondary" onClick={() => setAdding((a) => !a)}>
          {adding ? "Close" : "Register a rider"}
        </Button>
      }
    >
      {adding && <RegisterRider onDone={() => { setAdding(false); refresh(); }} />}
      <Tabs
        label="Riders"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "pending", label: `Applications (${count("pending")})` },
          { value: "active", label: `Active (${count("active")})` },
          { value: "suspended", label: `Suspended (${count("suspended")})` },
        ]}
      />
      {riders.error && <Alert>{riders.error.message}</Alert>}
      {!riders.data && !riders.error && <Loading />}
      {riders.data && rows.length === 0 && <p className="empty">No riders here.</p>}
      {!!rows.length && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Rider</th>
                <th>Tricycle</th>
                <th>Channel</th>
                <th>{filter === "active" ? "Duty" : "Area"}</th>
                <th>Owed</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <RiderRows key={r.id} rider={r} expanded={open === r.id} toggle={() => setOpen(open === r.id ? undefined : r.id)} onDone={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}

function RiderRows({
  rider,
  expanded,
  toggle,
  onDone,
}: {
  rider: RiderRow;
  expanded: boolean;
  toggle: () => void;
  onDone: () => void;
}) {
  const status = useAction(onDone);
  return (
    <>
      <tr>
        <td>
          <strong>{rider.name ?? "No name"}</strong>
          <div className="muted small">{rider.phone_e164}</div>
        </td>
        <td>{rider.tricycle_plate ?? "–"}</td>
        <td>{rider.channel === "ussd" ? "Feature phone" : "App"}</td>
        <td>
          {rider.status === "active"
            ? rider.on_duty
              ? `On duty${rider.last_location_at ? `, seen ${ago(rider.last_location_at)}` : ""}`
              : "Off duty"
            : (rider.work_area ?? "–")}
        </td>
        <td>{cedis(rider.owed_pesewas)}</td>
        <td className="actions">
          {rider.status === "pending" && (
            <>
              <Button busy={status.busy} onClick={() => status.run(() => admin.setRiderActive(rider.id, true))}>
                Approve
              </Button>
              <Button variant="danger" busy={status.busy} onClick={() => status.run(() => admin.setRiderActive(rider.id, false))}>
                Turn down
              </Button>
            </>
          )}
          {rider.status === "suspended" && (
            <Button variant="secondary" busy={status.busy} onClick={() => status.run(() => admin.setRiderActive(rider.id, true))}>
              Reactivate
            </Button>
          )}
          {rider.status === "active" && (
            <Button variant="quiet" onClick={toggle} aria-expanded={expanded}>
              {expanded ? "Close" : "Manage"}
            </Button>
          )}
          {status.error && <div className="error-text">{status.error}</div>}
        </td>
      </tr>
      {expanded && (
        <tr className="detail-row">
          <td colSpan={6}>
            <ManageRider rider={rider} onDone={onDone} />
          </td>
        </tr>
      )}
    </>
  );
}

function ManageRider({ rider, onDone }: { rider: RiderRow; onDone: () => void }) {
  const payable = usePoll(() => admin.payable(rider.id), 0, [rider.id]);
  const [amount, setAmount] = useState("");
  const [number, setNumber] = useState("");
  const pay = useAction(() => {
    setAmount("");
    void payable.reload();
    onDone();
  });
  const payoutNumber = useAction(() => {
    setNumber("");
    onDone();
  });
  const suspend = useAction(onDone);
  const pesewas = Math.round(Number(amount) * 100);

  return (
    <div className="grid-3">
      <div className="stack">
        <strong>Pay out</strong>
        <p className="small">
          Payable now: {payable.data ? cedis(payable.data.payable_pesewas) : "…"} (earnings older than the
          dispute hold). Goes to {rider.payout_msisdn ?? "no number set"}.
        </p>
        <Field id={`amt-${rider.id}`} label="Amount (GH₵)">
          <input id={`amt-${rider.id}`} inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} />
        </Field>
        {pay.error && <Alert>{pay.error}</Alert>}
        <Button
          disabled={!(pesewas > 0)}
          busy={pay.busy}
          onClick={() => pay.run(() => admin.requestPayout(rider.id, pesewas))}
        >
          Request payout
        </Button>
        <p className="hint">Large amounts wait for a second supervisor in Payouts.</p>
      </div>
      <div className="stack">
        <strong>Payout number</strong>
        <Field id={`num-${rider.id}`} label="New MoMo number" hint="The rider is texted, and payouts are held for 48 hours after a change.">
          <input id={`num-${rider.id}`} type="tel" value={number} onChange={(e) => setNumber(e.target.value)} />
        </Field>
        {payoutNumber.error && <Alert>{payoutNumber.error}</Alert>}
        <Button
          variant="secondary"
          disabled={!ghanaPhone(number)}
          busy={payoutNumber.busy}
          onClick={() => payoutNumber.run(() => admin.setPayoutNumber(rider.id, ghanaPhone(number)!))}
        >
          Change number
        </Button>
      </div>
      <div className="stack">
        <strong>Suspend</strong>
        <p className="small">
          Takes the rider off duty and withdraws any offer. Jobs they're on go to the review queue.
          {rider.missed_offers > 0 && ` Missed offers recently: ${rider.missed_offers}.`}
        </p>
        {suspend.error && <Alert>{suspend.error}</Alert>}
        <Button variant="danger" busy={suspend.busy} onClick={() => suspend.run(() => admin.setRiderActive(rider.id, false))}>
          Suspend rider
        </Button>
      </div>
    </div>
  );
}

function RegisterRider({ onDone }: { onDone: () => void }) {
  const me = useMe();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [channel, setChannel] = useState<"ussd" | "pwa">("ussd");
  const [consent, setConsent] = useState(false);
  const action = useAction(onDone);
  const e164 = ghanaPhone(phone);

  return (
    <Card>
      <h2 className="card-title">Register a rider</h2>
      <p className="small muted">
        For riders without a smartphone, or anyone you sign up in person. They're active straight away.
      </p>
      <div className="grid-3">
        <Field id="new-name" label="Name">
          <input id="new-name" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="new-phone" label="Phone (also the payout number)">
          <input id="new-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </Field>
        <Field id="new-channel" label="Phone type">
          <select id="new-channel" value={channel} onChange={(e) => setChannel(e.target.value as "ussd" | "pwa")}>
            <option value="ussd">Feature phone (USSD and SMS)</option>
            <option value="pwa">Smartphone (app)</option>
          </select>
        </Field>
      </div>
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>The rider signed consent form {me.consent_version}.</span>
      </label>
      {action.error && <Alert>{action.error}</Alert>}
      <Button
        disabled={!consent || !e164 || name.trim().length < 1}
        busy={action.busy}
        onClick={() =>
          action.run(() =>
            admin.registerRider({ phone: e164!, name: name.trim(), channel, consent_version: me.consent_version }),
          )
        }
      >
        Register
      </Button>
    </Card>
  );
}
