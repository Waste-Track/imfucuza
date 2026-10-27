import { Link } from "react-router";
import { Icon } from "../../components/Icon";
import { Alert, HouseholdNav, Loading, Screen } from "../../components/ui";
import { api } from "../../lib/api";
import { headline } from "../../lib/pickupStatus";
import { usePoll } from "../../lib/usePoll";

const DATE = new Intl.DateTimeFormat("en-GH", {
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
});

export function Requests() {
  const { data, error } = usePoll(api.pickups, 30_000);
  return (
    <Screen title="Requests" nav={<HouseholdNav />}>
      {error && (data ? <Alert tone="warning">Offline. Showing the last update.</Alert> : <Alert>{error.message}</Alert>)}
      {!data && !error && <Loading />}
      {data?.length === 0 && <p className="muted">No requests yet.</p>}
      <ul className="list">
        {data?.map((p) => (
          <li key={p.id}>
            <Link to={`/pickups/${p.id}`} className="list-item tap">
              <div>
                <strong>{p.offering === "plastic" ? "Plastic drop-off" : "Waste pickup"}</strong>
                <div className="muted small">
                  {headline(p.status, p.offering)} · {DATE.format(new Date(p.requested_at))}
                </div>
              </div>
              <Icon name="forward" />
            </Link>
          </li>
        ))}
      </ul>
    </Screen>
  );
}
