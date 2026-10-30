import { Alert, Card, Loading, RiderNav, Screen } from "../../components/ui";
import { api } from "../../lib/api";
import { cedis } from "../../lib/format";
import type { PastJob } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";

const OUTCOMES: Partial<Record<PastJob["status"], string>> = {
  completed: "Completed",
  cancelled: "Cancelled",
  rejected: "Plastic not accepted",
  unconfirmed: "Not confirmed by the household",
  expired: "Expired",
  failed: "Couldn't be completed",
  refunded: "Refunded to the household",
  disputed: "With a supervisor",
};

const day = new Intl.DateTimeFormat("en-GH", { weekday: "short", day: "numeric", month: "short" });
const time = new Intl.DateTimeFormat("en-GH", { hour: "numeric", minute: "2-digit" });

export function RiderHistory() {
  const history = usePoll(api.history, 0);

  return (
    <Screen title="History" nav={<RiderNav />}>
      {history.error && <Alert>{history.error.message}</Alert>}
      {!history.data && !history.error && <Loading />}
      {history.data?.length === 0 && (
        <Card>
          <strong>No jobs yet</strong>
          <p className="muted small">Pickups you finish show up here with what you earned.</p>
        </Card>
      )}
      {!!history.data?.length && (
        <ul className="plain card flush">
          {history.data.map((job) => {
            const at = new Date(job.finished_at);
            return (
              <li key={job.pickup_id} className="row-between list-line pad-x">
                <div>
                  <strong>{job.offering === "plastic" ? "Plastic" : "General waste"}</strong>
                  <div className="muted small">
                    {day.format(at)}, {time.format(at)} · {OUTCOMES[job.status] ?? job.status}
                  </div>
                </div>
                <strong className={job.earned_pesewas ? "earning" : "muted"}>
                  {job.earned_pesewas ? `+${cedis(job.earned_pesewas)}` : "–"}
                </strong>
              </li>
            );
          })}
        </ul>
      )}
    </Screen>
  );
}
