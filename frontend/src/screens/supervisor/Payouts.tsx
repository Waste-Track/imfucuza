import { useState } from "react";
import { Alert, Button, Loading } from "../../components/ui";
import { cedis } from "../../lib/format";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, when, type PayoutRow, type PayoutStatus } from "./admin";
import { Page, Tabs } from "./Console";

export function Payouts({ onChange }: { onChange: () => void }) {
  const [status, setStatus] = useState<PayoutStatus>("pending_approval");
  const payouts = usePoll(() => admin.payouts(status), 30_000, [status]);
  const refresh = () => {
    void payouts.reload();
    onChange();
  };

  return (
    <Page
      title="Payouts"
      intro="Rider earnings are sent by hand over mobile money. Send the money to the number shown, then record the MoMo reference."
    >
      <Tabs
        label="Payout status"
        value={status}
        onChange={setStatus}
        options={[
          { value: "pending_approval", label: "Waiting for approval" },
          { value: "approved", label: "Approved, to send" },
          { value: "recorded", label: "Sent" },
          { value: "rejected", label: "Rejected" },
        ]}
      />
      {payouts.error && <Alert>{payouts.error.message}</Alert>}
      {!payouts.data && !payouts.error && <Loading />}
      {payouts.data?.length === 0 && <p className="empty">No payouts here.</p>}
      {!!payouts.data?.length && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Rider</th>
                <th>Amount</th>
                <th>Send to</th>
                <th>Requested</th>
                <th>{status === "recorded" ? "MoMo reference" : ""}</th>
              </tr>
            </thead>
            <tbody>
              {payouts.data.map((p) => (
                <PayoutRowView key={p.id} payout={p} onDone={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}

function PayoutRowView({ payout, onDone }: { payout: PayoutRow; onDone: () => void }) {
  const me = useMe();
  const action = useAction(onDone);
  const [reference, setReference] = useState("");
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const ownRequest = payout.requested_by === me.user_id;

  return (
    <tr>
      <td className="strong">{payout.rider_name ?? "Rider"}</td>
      <td>{cedis(payout.amount_pesewas)}</td>
      <td className="nowrap">{payout.destination_msisdn}</td>
      <td>
        {when(payout.created_at)}
        <div className="muted small">by {ownRequest ? "you" : (payout.requested_by_name ?? "a supervisor")}</div>
      </td>
      <td className="actions">
        {payout.status === "recorded" && payout.momo_reference}
        {payout.status === "pending_approval" &&
          (ownRequest ? (
            <span className="muted small">Another supervisor must approve</span>
          ) : (
            <Button busy={action.busy} onClick={() => action.run(() => admin.approvePayout(payout.id))}>
              Approve
            </Button>
          ))}
        {payout.status === "approved" && (
          <span className="inline-form">
            <label className="visually-hidden" htmlFor={`ref-${payout.id}`}>
              MoMo reference
            </label>
            <input
              id={`ref-${payout.id}`}
              className="input"
              placeholder="MoMo reference"
              value={reference}
              onChange={(e) => setReference(e.target.value)}
            />
            <Button
              disabled={reference.trim().length < 3}
              busy={action.busy}
              onClick={() => action.run(() => admin.recordPayout(payout.id, reference.trim()))}
            >
              Record sent
            </Button>
          </span>
        )}
        {(payout.status === "pending_approval" || payout.status === "approved") &&
          (rejecting ? (
            <span className="inline-form">
              <label className="visually-hidden" htmlFor={`why-${payout.id}`}>
                Reason
              </label>
              <input
                id={`why-${payout.id}`}
                className="input"
                placeholder="Why reject?"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
              />
              <Button
                variant="danger"
                disabled={reason.trim().length < 3}
                busy={action.busy}
                onClick={() => action.run(() => admin.rejectPayout(payout.id, reason.trim()))}
              >
                Reject
              </Button>
            </span>
          ) : (
            <Button variant="quiet" onClick={() => setRejecting(true)}>
              Reject…
            </Button>
          ))}
        {action.error && <div className="error-text">{action.error}</div>}
      </td>
    </tr>
  );
}
