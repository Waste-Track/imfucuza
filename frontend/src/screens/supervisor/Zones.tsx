import { useState } from "react";
import { Alert, Button, Card, Field, Loading } from "../../components/ui";
import { distance } from "../../lib/format";
import { currentFix } from "../../lib/geo";
import { useAction } from "../../lib/useAction";
import { usePoll } from "../../lib/usePoll";
import { admin, type Zone } from "./admin";
import { Page } from "./Console";

export function Zones() {
  const zones = usePoll(admin.zones, 0);
  return (
    <Page
      title="Zones and landmarks"
      intro="Feature-phone riders pick where they are from these menus, and leaderboards are per zone. Add zones and landmarks while standing at them, or type the coordinates."
    >
      <NewPlace title="New zone" withRadius onSave={(p) => admin.createZone({ ...p, radius_m: p.radius_m! })} onDone={zones.reload} />
      {zones.error && <Alert>{zones.error.message}</Alert>}
      {!zones.data && !zones.error && <Loading />}
      {zones.data?.length === 0 && <p className="empty">No zones yet. USSD riders can't be dispatched until there are some.</p>}
      {zones.data?.map((zone) => <ZoneCard key={zone.id} zone={zone} onDone={zones.reload} />)}
    </Page>
  );
}

function ZoneCard({ zone, onDone }: { zone: Zone; onDone: () => void }) {
  const [adding, setAdding] = useState(false);
  return (
    <Card>
      <div className="row-between">
        <div>
          <h2 className="card-title">
            {zone.ussd_index}. {zone.name}
          </h2>
          <div className="muted small">
            {zone.lat.toFixed(5)}, {zone.lng.toFixed(5)} · radius {distance(zone.radius_m)}
          </div>
        </div>
        <Button variant="secondary" onClick={() => setAdding((a) => !a)}>
          {adding ? "Close" : "Add landmark"}
        </Button>
      </div>
      {zone.landmarks.length ? (
        <ol className="plain small">
          {zone.landmarks.map((l) => (
            <li key={l.id}>
              {l.ussd_index}. {l.name} <span className="muted">({l.lat.toFixed(5)}, {l.lng.toFixed(5)})</span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted small">No landmarks yet.</p>
      )}
      {adding && (
        <NewPlace
          title={`New landmark in ${zone.name}`}
          onSave={(p) => admin.addLandmark(zone.id, p)}
          onDone={() => {
            setAdding(false);
            onDone();
          }}
        />
      )}
    </Card>
  );
}

type PlaceInput = { name: string; lat: number; lng: number; radius_m?: number };

function NewPlace({
  title,
  withRadius,
  onSave,
  onDone,
}: {
  title: string;
  withRadius?: boolean;
  onSave: (place: PlaceInput) => Promise<unknown>;
  onDone: () => void;
}) {
  const [name, setName] = useState("");
  const [lat, setLat] = useState("");
  const [lng, setLng] = useState("");
  const [radius, setRadius] = useState("800");
  const action = useAction(() => {
    setName("");
    setLat("");
    setLng("");
    onDone();
  });
  const locate = useAction();

  const place: PlaceInput = { name: name.trim(), lat: Number(lat), lng: Number(lng), radius_m: Number(radius) };
  const valid =
    place.name.length >= 2 &&
    lat !== "" &&
    lng !== "" &&
    Math.abs(place.lat) <= 90 &&
    Math.abs(place.lng) <= 180 &&
    (!withRadius || place.radius_m! > 0);

  return (
    <Card className="nested">
      <h3 className="card-title">{title}</h3>
      <div className={withRadius ? "grid-4" : "grid-3"}>
        <Field id={`${title}-name`} label="Name">
          <input id={`${title}-name`} maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id={`${title}-lat`} label="Latitude">
          <input id={`${title}-lat`} inputMode="decimal" value={lat} onChange={(e) => setLat(e.target.value)} />
        </Field>
        <Field id={`${title}-lng`} label="Longitude">
          <input id={`${title}-lng`} inputMode="decimal" value={lng} onChange={(e) => setLng(e.target.value)} />
        </Field>
        {withRadius && (
          <Field id={`${title}-r`} label="Radius (m)">
            <input id={`${title}-r`} inputMode="numeric" value={radius} onChange={(e) => setRadius(e.target.value)} />
          </Field>
        )}
      </div>
      {(action.error || locate.error) && <Alert>{action.error ?? locate.error}</Alert>}
      <div className="button-row">
        <Button
          variant="secondary"
          busy={locate.busy}
          onClick={() =>
            locate.run(async () => {
              const fix = await currentFix();
              setLat(fix.lat.toFixed(6));
              setLng(fix.lng.toFixed(6));
            })
          }
        >
          Use my location
        </Button>
        <Button disabled={!valid} busy={action.busy} onClick={() => action.run(() => onSave(place))}>
          Save
        </Button>
      </div>
    </Card>
  );
}
