import type { ReactNode } from "react";
import { Navigate, NavLink, Route, Routes } from "react-router";
import { Button } from "../../components/ui";
import { usePoll } from "../../lib/usePoll";
import { useSession } from "../../session";
import { admin } from "./admin";
import { Approvals } from "./Approvals";
import { Payouts } from "./Payouts";
import { Queue } from "./Queue";
import { ReviewItem } from "./ReviewItem";
import { Rewards } from "./Rewards";
import { Riders } from "./Riders";
import { Zones } from "./Zones";

/** The supervisor console: desktop first, but usable on a phone in the field. */
export function Console() {
  const { me, signOut } = useSession();
  const queue = usePoll(() => admin.reviewItems("open"), 30_000);
  const riders = usePoll(admin.riders, 60_000);
  const payouts = usePoll(() => admin.payouts("pending_approval"), 60_000);
  const decisions = usePoll(admin.decisions, 60_000);

  const applications = riders.data?.filter((r) => r.status === "pending").length;

  return (
    <div className="console">
      <aside className="console-nav">
        <div className="console-brand">
          <div className="eyebrow light">IMFUCUZA</div>
          <strong>Supervisor</strong>
        </div>
        <nav aria-label="Console">
          <Item to="/" count={queue.data?.length}>
            Review queue
          </Item>
          <Item to="/riders" count={applications}>
            Riders
          </Item>
          <Item to="/payouts" count={payouts.data?.length}>
            Payouts
          </Item>
          <Item to="/approvals" count={decisions.data?.length}>
            Approvals
          </Item>
          <Item to="/zones">Zones</Item>
          <Item to="/rewards">Rewards</Item>
        </nav>
        <div className="console-me">
          <span>{me?.name ?? "Signed in"}</span>
          <Button variant="quiet" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </aside>
      <main className="console-main">
        <Routes>
          <Route path="/" element={<Queue onChange={queue.reload} />} />
          <Route path="/queue/:id" element={<ReviewItem onChange={queue.reload} />} />
          <Route path="/riders" element={<Riders onChange={riders.reload} />} />
          <Route path="/payouts" element={<Payouts onChange={payouts.reload} />} />
          <Route path="/approvals" element={<Approvals onChange={decisions.reload} />} />
          <Route path="/zones" element={<Zones />} />
          <Route path="/rewards" element={<Rewards />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Item({ to, count, children }: { to: string; count?: number; children: ReactNode }) {
  return (
    <NavLink to={to} end={to === "/"}>
      <span>{children}</span>
      {!!count && (
        <span className="badge" aria-label={`${count} waiting`}>
          {count}
        </span>
      )}
    </NavLink>
  );
}

export function Page({
  title,
  intro,
  actions,
  children,
}: {
  title: string;
  intro?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {intro && <p className="muted">{intro}</p>}
        </div>
        {actions}
      </header>
      {children}
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented tabs" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
