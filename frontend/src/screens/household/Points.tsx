import { useState } from "react";
import { Alert, Button, Card, Field, HouseholdNav, Loading, Screen } from "../../components/ui";
import { api, ApiError } from "../../lib/api";
import { cedis } from "../../lib/format";
import type { Product } from "../../lib/types";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";

export function Points() {
  const points = usePoll(api.points, 0);
  const board = usePoll(api.leaderboard, 0);
  const catalogue = usePoll(api.catalogue, 0);
  const vouchers = usePoll(api.vouchers, 0);

  const refresh = () => {
    void points.reload();
    void board.reload();
    void vouchers.reload();
  };

  return (
    <Screen title="Points" nav={<HouseholdNav />}>
      <Card tone="amber">
        <div className="small strong">Your points</div>
        <div className="huge">{points.data?.available ?? "…"}</div>
        {!!points.data?.pending && (
          <div className="small">{points.data.pending} more once a supervisor checks your plastic</div>
        )}
      </Card>

      <section className="card flush" aria-labelledby="board-title">
        <h2 id="board-title" className="section-title">
          {board.data ? `${board.data.zone.name.toUpperCase()}, THIS MONTH` : "LEADERBOARD"}
        </h2>
        {board.error?.status === 404 && (
          <p className="pad muted">Your home isn't in a collection zone yet.</p>
        )}
        {!board.data && !board.error && <Loading />}
        {board.data && (
          <ol className="board">
            {board.data.top.map((row) => (
              <li key={`${row.rank}-${row.name}`}>
                <span className="rank">{row.rank}</span>
                <span className="grow">{row.name}</span>
                <strong>{row.points.toLocaleString()}</strong>
              </li>
            ))}
            {board.data.top.length === 0 && <li className="muted">No one has earned points yet this month.</li>}
            {board.data.you && !board.data.you.on_board && (
              <li className="you">
                <span className="rank">{board.data.you.rank}</span>
                <span className="grow">You (hidden)</span>
                <strong>{board.data.you.points.toLocaleString()}</strong>
              </li>
            )}
          </ol>
        )}
        {board.data?.you && !board.data.you.on_board && <JoinBoard onDone={refresh} />}
      </section>

      <section className="stack" aria-labelledby="vouchers-title">
        <h2 id="vouchers-title" className="section-title">
          VOUCHERS
        </h2>
        {vouchers.data
          ?.filter((v) => v.code)
          .map((v) => (
            <Card key={v.id} tone="green">
              <strong>{v.product}</strong>
              <div className="voucher-code">{v.code}</div>
              <div className="small">
                Show this code at the shop. Use by{" "}
                {new Date(v.expires_at).toLocaleDateString("en-GH", { day: "numeric", month: "short" })}.
              </div>
            </Card>
          ))}
        {catalogue.data?.length === 0 && <p className="muted">No vouchers on offer right now.</p>}
        {catalogue.data?.map((product) => (
          <BuyVoucher
            key={product.id}
            product={product}
            available={points.data?.available ?? 0}
            onDone={refresh}
          />
        ))}
      </section>
    </Screen>
  );
}

function BuyVoucher({
  product,
  available,
  onDone,
}: {
  product: Product;
  available: number;
  onDone: () => void;
}) {
  const [key] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string>();

  const buy = async () => {
    setBusy(true);
    setMessage(undefined);
    try {
      await api.buyVoucher(product.id, key);
      setMessage("Done. Your code is above and on its way by SMS.");
      onDone();
    } catch (e) {
      setMessage(e instanceof ApiError ? e.message : "Couldn't get the voucher.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <div className="row-between">
        <div>
          <strong>{product.name}</strong>
          <div className="muted small">
            Worth {cedis(product.face_value_pesewas)} · {product.points_cost} points
          </div>
        </div>
        <Button variant="secondary" busy={busy} disabled={available < product.points_cost} onClick={buy}>
          Get
        </Button>
      </div>
      {message && <p className="small">{message}</p>}
    </Card>
  );
}

function JoinBoard({ onDone }: { onDone: () => void }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const join = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await api.joinLeaderboard(name.trim(), me.leaderboard_consent_version);
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't join.");
      setBusy(false);
    }
  };

  if (!open) {
    return (
      <div className="pad">
        <Button variant="secondary" onClick={() => setOpen(true)}>
          Show my name on the board
        </Button>
      </div>
    );
  }
  return (
    <div className="pad stack">
      <Field id="board-name" label="Name to show" hint="Your neighbours in this zone will see it next to your points.">
        <input id="board-name" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      {error && <Alert>{error}</Alert>}
      <Button busy={busy} disabled={name.trim().length < 2} onClick={join}>
        Join the leaderboard
      </Button>
    </div>
  );
}
