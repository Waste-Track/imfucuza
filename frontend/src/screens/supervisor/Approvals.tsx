import { Alert, Button, Loading } from "../../components/ui";
import { cedis } from "../../lib/format";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, when, type DecisionRow } from "./admin";
import { Page } from "./Console";

const OUTCOME_LABELS = {
  reissue_pin: "New PIN",
  refund: "Refund",
  grant_points: "Grant points",
  dismiss: "Dismiss",
} as const;

export function Approvals({ onChange }: { onChange: () => void }) {
  const decisions = usePoll(admin.decisions, 30_000);
  const refresh = () => {
    void decisions.reload();
    onChange();
  };

  return (
    <Page
      title="Approvals"
      intro="Complaint decisions that refund money or grant points past one supervisor's daily limit. A different supervisor must approve them."
    >
      {decisions.error && <Alert>{decisions.error.message}</Alert>}
      {!decisions.data && !decisions.error && <Loading />}
      {decisions.data?.length === 0 && <p className="empty">Nothing waiting for approval.</p>}
      {!!decisions.data?.length && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Decision</th>
                <th>Note</th>
                <th>Decided by</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {decisions.data.map((d) => (
                <DecisionRowView key={d.id} decision={d} onDone={refresh} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}

function DecisionRowView({ decision, onDone }: { decision: DecisionRow; onDone: () => void }) {
  const me = useMe();
  const action = useAction(onDone);
  const own = decision.decided_by === me.user_id;
  return (
    <tr>
      <td className="strong">
        {OUTCOME_LABELS[decision.outcome]}
        {decision.amount != null &&
          (decision.outcome === "refund" ? ` ${cedis(decision.amount)}` : ` ${decision.amount} points`)}
      </td>
      <td>{decision.note}</td>
      <td>
        {own ? "You" : (decision.decided_by_name ?? "A supervisor")}
        <div className="muted small">{when(decision.created_at)}</div>
      </td>
      <td className="actions">
        {own ? (
          <span className="muted small">Another supervisor must approve</span>
        ) : (
          <>
            <Button busy={action.busy} onClick={() => action.run(() => admin.approveDecision(decision.id))}>
              Approve
            </Button>
            <Button variant="danger" busy={action.busy} onClick={() => action.run(() => admin.rejectDecision(decision.id))}>
              Reject
            </Button>
          </>
        )}
        {action.error && <div className="error-text">{action.error}</div>}
      </td>
    </tr>
  );
}
