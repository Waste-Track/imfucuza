import { Link } from "react-router";
import { Icon } from "../../components/Icon";
import { Alert, HouseholdNav, Screen } from "../../components/ui";
import { api } from "../../lib/api";
import { cedis } from "../../lib/format";
import { headline, isEnded } from "../../lib/pickupStatus";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";

function greeting(hour: number): string {
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

export function Home() {
  const me = useMe();
  const pickups = usePoll(api.pickups, 15_000);
  const points = usePoll(api.points, 0);
  const active = pickups.data?.filter((p) => !isEnded(p.status)) ?? [];

  return (
    <Screen
      title={`${greeting(new Date().getHours())}${me.name ? `, ${me.name.split(" ")[0]}` : ""}`}
      eyebrow="Madina, Accra"
      nav={<HouseholdNav />}
    >
      {pickups.error && !pickups.data && <Alert>{pickups.error.message}</Alert>}

      {active.map((p) => (
        <Link key={p.id} to={`/pickups/${p.id}`} className="card card-green tap">
          <div className="eyebrow">{p.offering === "plastic" ? "PLASTIC DROP-OFF" : "ACTIVE REQUEST"}</div>
          <div className="row-between">
            <strong className="big">{headline(p.status, p.offering)}</strong>
            <Icon name="forward" />
          </div>
        </Link>
      ))}

      <Link to="/request" className="hero tap">
        <div>
          <div className="hero-title">Request a pickup</div>
          <div className="hero-sub">A rider collects general waste from your door</div>
        </div>
        <div className="row-between">
          <span className="chip">{cedis(me.refuse_fee_pesewas)}</span>
          <Icon name="forward" />
        </div>
      </Link>

      <Link to="/request?type=plastic" className="card tap">
        <strong className="big">Drop off plastic</strong>
        <div className="muted">Sorted plastic earns points by weight</div>
      </Link>

      <Link to="/points" className="card tap row">
        <span className="points-badge">{points.data ? points.data.available : "…"} pts</span>
        <span className="muted">
          {points.data?.pending
            ? `${points.data.pending} more waiting for a check`
            : "See the leaderboard and vouchers"}
        </span>
      </Link>
    </Screen>
  );
}
