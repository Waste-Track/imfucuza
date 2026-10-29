import type { ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { Icon, type IconName } from "./Icon";

export function Screen({
  title,
  back,
  eyebrow,
  dark,
  actions,
  footer,
  nav,
  children,
}: {
  title: string;
  // A path, or true to go back in history.
  back?: string | true;
  eyebrow?: string;
  dark?: boolean;
  actions?: ReactNode;
  footer?: ReactNode;
  nav?: ReactNode;
  children: ReactNode;
}) {
  const navigate = useNavigate();
  return (
    <div className={dark ? "screen screen-dark" : "screen"}>
      <header className={back ? "bar" : "bar bar-large"}>
        {back && (
          <button
            type="button"
            className="icon-button"
            aria-label="Back"
            onClick={() => (back === true ? navigate(-1) : navigate(back))}
          >
            <Icon name="back" />
          </button>
        )}
        <div className="bar-title">
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h1>{title}</h1>
        </div>
        {actions}
      </header>
      <main className="content">{children}</main>
      {footer && <div className="footer">{footer}</div>}
      {nav}
    </div>
  );
}

export function BottomNav({ items }: { items: { to: string; label: string; icon: IconName }[] }) {
  return (
    <nav className="bottom-nav" aria-label="Main">
      {items.map((item) => (
        <NavLink key={item.to} to={item.to} end={item.to === "/"}>
          <Icon name={item.icon} />
          {item.label}
        </NavLink>
      ))}
    </nav>
  );
}

export function HouseholdNav() {
  return (
    <BottomNav
      items={[
        { to: "/", label: "Home", icon: "home" },
        { to: "/pickups", label: "Requests", icon: "list" },
        { to: "/points", label: "Points", icon: "trophy" },
        { to: "/account", label: "Account", icon: "person" },
      ]}
    />
  );
}

export function Button({
  children,
  variant = "primary",
  busy,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "quiet";
  busy?: boolean;
}) {
  return (
    <button
      type="button"
      {...rest}
      className={`button button-${variant} ${rest.className ?? ""}`}
      disabled={rest.disabled || busy}
      aria-busy={busy || undefined}
    >
      {busy ? <span className="spinner" aria-hidden="true" /> : null}
      {children}
    </button>
  );
}

export function ButtonLink({
  to,
  children,
  variant = "primary",
}: {
  to: string;
  children: ReactNode;
  variant?: "primary" | "secondary";
}) {
  return (
    <Link to={to} className={`button button-${variant}`}>
      {children}
    </Link>
  );
}

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {children}
      {hint && (
        <div className="hint" id={`${id}-hint`}>
          {hint}
        </div>
      )}
    </div>
  );
}

export function Alert({
  children,
  tone = "error",
}: {
  children: ReactNode;
  tone?: "error" | "info" | "warning";
}) {
  return (
    <div className={`alert alert-${tone}`} role={tone === "error" ? "alert" : "status"}>
      {children}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spinner" aria-hidden="true" />
      {label}
    </div>
  );
}

export function Card({
  children,
  tone,
  className,
}: {
  children: ReactNode;
  tone?: "green" | "amber" | "cream" | "dark";
  className?: string;
}) {
  return <div className={`card ${tone ? `card-${tone}` : ""} ${className ?? ""}`}>{children}</div>;
}
