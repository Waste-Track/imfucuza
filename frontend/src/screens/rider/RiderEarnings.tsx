import { Alert, Card, Loading, RiderNav, Screen } from "../../components/ui";
import { api } from "../../lib/api";
import { cedis } from "../../lib/format";
import type { Payout } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";

const PAYOUT_STATES: Record<Payout["status"], string> = {
  pending_approval: "Waiting for approval",
  approved: "Approved, sending soon",
  recorded: "Paid",
  rejected: "Not sent",
};

const date = new Intl.DateTimeFormat("en-GH", { day: "numeric", month: "short", year: "numeric" });

export function RiderEarnings() {
  const earnings = usePoll(api.earnings, 60_000);
  const data = earnings.data;

  return (
    <Screen title="Earnings" nav={<RiderNav />}>
      {earnings.error && <Alert>{earnings.error.message}</Alert>}
      {!data && !earnings.error && <Loading />}
      {data && (
        <>
          <Card tone="green">
            <div className="small strong">Owed to you</div>
            <div className="huge">{cedis(data.owed_pesewas)}</div>
            <div className="small">
              Paid to your MoMo number{data.payout_phone ? ` ${data.payout_phone}` : ""} by the Imfucuza supervisor.
            </div>
          </Card>

          <div className="today">
            <div className="stat">
              <div className="stat-value">{cedis(data.today_pesewas)}</div>
              <div className="stat-label">Earned today</div>
            </div>
            <div className="stat">
              <div className="stat-value">{cedis(data.week_pesewas)}</div>
              <div className="stat-label">This week</div>
            </div>
            <div className="stat">
              <div className="stat-value">{data.jobs_today}</div>
              <div className="stat-label">Jobs today</div>
            </div>
          </div>

          <section className="card flush" aria-labelledby="payouts-title">
            <h2 id="payouts-title" className="section-title">
              PAYMENTS TO YOU
            </h2>
            {data.payouts.length === 0 ? (
              <p className="pad muted small">No payments yet.</p>
            ) : (
              <ul className="plain">
                {data.payouts.map((payout) => (
                  <li key={payout.id} className="row-between list-line pad-x">
                    <div>
                      <strong>{cedis(payout.amount_pesewas)}</strong>
                      <div className="muted small">
                        {date.format(new Date(payout.recorded_at ?? payout.created_at))}
                      </div>
                    </div>
                    <span className={payout.status === "recorded" ? "chip" : "chip chip-cream"}>
                      {PAYOUT_STATES[payout.status]}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </Screen>
  );
}
