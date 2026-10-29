import { useState } from "react";
import { Link } from "react-router";
import { Alert, Loading } from "../../components/ui";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, ago, REVIEW_LABELS, type ReviewType } from "./admin";
import { Page, Tabs } from "./Console";

export function Queue({ onChange }: { onChange: () => void }) {
  const me = useMe();
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const [type, setType] = useState<ReviewType | "">("");
  const items = usePoll(
    async () => {
      const rows = await admin.reviewItems(status, type || undefined);
      if (status === "open") onChange();
      return rows;
    },
    status === "open" ? 20_000 : 0,
    [status, type],
  );

  return (
    <Page
      title="Review queue"
      intro="Pickups the Engine wants a person to look at. Claim one before acting on it, so two supervisors don't."
      actions={
        <div className="row">
          <label className="visually-hidden" htmlFor="type">
            Type
          </label>
          <select id="type" className="select" value={type} onChange={(e) => setType(e.target.value as ReviewType | "")}>
            <option value="">All types</option>
            {Object.entries(REVIEW_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <Tabs
            label="Status"
            value={status}
            onChange={setStatus}
            options={[
              { value: "open", label: "Open" },
              { value: "resolved", label: "Resolved" },
            ]}
          />
        </div>
      }
    >
      {items.error && <Alert>{items.error.message}</Alert>}
      {!items.data && !items.error && <Loading />}
      {items.data?.length === 0 && (
        <p className="empty">{status === "open" ? "Nothing waiting. " : ""}No items here.</p>
      )}
      {!!items.data?.length && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Type</th>
                <th>Why</th>
                <th>Waiting</th>
                <th>Claimed by</th>
              </tr>
            </thead>
            <tbody>
              {items.data.map((item) => (
                <tr key={item.id}>
                  <td>
                    <Link to={`/queue/${item.id}`} className="strong link">
                      {REVIEW_LABELS[item.type] ?? item.type}
                    </Link>
                  </td>
                  <td>{item.reason ?? "–"}</td>
                  <td className="nowrap">{ago(item.created_at)}</td>
                  <td>
                    {item.assigned_to
                      ? item.assigned_to === me.user_id
                        ? "You"
                        : (item.assigned_to_name ?? "Another supervisor")
                      : "–"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Page>
  );
}
