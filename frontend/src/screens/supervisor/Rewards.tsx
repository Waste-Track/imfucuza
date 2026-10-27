import { useState } from "react";
import { Alert, Button, Card, Field, Loading } from "../../components/ui";
import { cedis, ghanaPhone } from "../../lib/format";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { useMe } from "../../session";
import { admin, type ProductRow } from "./admin";
import { Page } from "./Console";

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function Rewards() {
  const products = usePoll(admin.products, 0);
  const [month, setMonth] = useState(lastMonth);
  const report = usePoll(() => admin.shopReport(month), 0, [month]);

  return (
    <Page title="Rewards" intro="What households can spend points on, partner shops, and what each shop redeemed.">
      <div className="grid-2">
        <div className="stack">
          <Card>
            <h2 className="card-title">Vouchers on offer</h2>
            {products.error && <Alert>{products.error.message}</Alert>}
            {!products.data && !products.error && <Loading />}
            {products.data?.length === 0 && <p className="muted small">None yet.</p>}
            <ul className="plain">
              {products.data?.map((p) => <ProductItem key={p.id} product={p} onDone={products.reload} />)}
            </ul>
          </Card>
          <NewProduct onDone={products.reload} />
        </div>
        <div className="stack">
          <Card>
            <div className="row-between">
              <h2 className="card-title">Redeemed by shop</h2>
              <label className="visually-hidden" htmlFor="month">
                Month
              </label>
              <input id="month" className="input" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
            </div>
            {report.error && <Alert>{report.error.message}</Alert>}
            {report.data?.length === 0 && <p className="muted small">No vouchers redeemed that month.</p>}
            {!!report.data?.length && (
              <table className="table compact">
                <thead>
                  <tr>
                    <th>Shop</th>
                    <th>Vouchers</th>
                    <th>Owed to shop</th>
                  </tr>
                </thead>
                <tbody>
                  {report.data.map((r) => (
                    <tr key={r.shop_id}>
                      <td>{r.shop}</td>
                      <td>{r.vouchers}</td>
                      <td>{cedis(r.face_value_pesewas)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>
          <RegisterShop />
        </div>
      </div>
    </Page>
  );
}

function ProductItem({ product, onDone }: { product: ProductRow; onDone: () => void }) {
  const action = useAction(onDone);
  return (
    <li className="row-between list-line">
      <div>
        <strong className={product.active ? "" : "muted"}>{product.name}</strong>
        <div className="muted small">
          {product.points_cost} points · worth {cedis(product.face_value_pesewas)}
          {!product.active && " · hidden"}
        </div>
        {action.error && <div className="error-text">{action.error}</div>}
      </div>
      <Button variant="quiet" busy={action.busy} onClick={() => action.run(() => admin.setProductActive(product.id, !product.active))}>
        {product.active ? "Hide" : "Offer again"}
      </Button>
    </li>
  );
}

function NewProduct({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [points, setPoints] = useState("");
  const [value, setValue] = useState("");
  const action = useAction(() => {
    setName("");
    setPoints("");
    setValue("");
    onDone();
  });
  const pointsCost = Math.round(Number(points));
  const pesewas = Math.round(Number(value) * 100);
  return (
    <Card>
      <h2 className="card-title">Add a voucher</h2>
      <div className="grid-3">
        <Field id="p-name" label="Name">
          <input id="p-name" maxLength={40} placeholder="GH₵ 5 airtime" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="p-points" label="Points">
          <input id="p-points" inputMode="numeric" value={points} onChange={(e) => setPoints(e.target.value)} />
        </Field>
        <Field id="p-value" label="Worth (GH₵)">
          <input id="p-value" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
        </Field>
      </div>
      {action.error && <Alert>{action.error}</Alert>}
      <Button
        disabled={name.trim().length < 2 || !(pointsCost > 0) || !(pesewas >= 0) || value === ""}
        busy={action.busy}
        onClick={() => action.run(() => admin.addProduct({ name: name.trim(), points_cost: pointsCost, face_value_pesewas: pesewas }))}
      >
        Add voucher
      </Button>
    </Card>
  );
}

function RegisterShop() {
  const me = useMe();
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  const [done, setDone] = useState<string>();
  const action = useAction(() => {
    setDone(`${name.trim()} can now redeem vouchers by USSD from ${phone}.`);
    setName("");
    setPhone("");
    setConsent(false);
  });
  const e164 = ghanaPhone(phone);
  return (
    <Card>
      <h2 className="card-title">Register a partner shop</h2>
      <div className="grid-2">
        <Field id="s-name" label="Shop name">
          <input id="s-name" maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id="s-phone" label="Shop phone (redeems by USSD)">
          <input id="s-phone" type="tel" value={phone} onChange={(e) => setPhone(e.target.value)} />
        </Field>
      </div>
      <label className="check">
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        <span>The shop signed consent form {me.consent_version}.</span>
      </label>
      {action.error && <Alert>{action.error}</Alert>}
      {done && <Alert tone="info">{done}</Alert>}
      <Button
        disabled={!consent || !e164 || name.trim().length < 2}
        busy={action.busy}
        onClick={() => action.run(() => admin.registerShop({ phone: e164!, name: name.trim(), consent_version: me.consent_version }))}
      >
        Register shop
      </Button>
    </Card>
  );
}
