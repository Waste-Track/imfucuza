import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";

export type MapPin = { lat: number; lng: number; label: string };

// Madina, until the phone reports where the rider is.
const MADINA: L.LatLngTuple = [5.6837, -0.1657];

const dot = (fill: string, radius: number): L.CircleMarkerOptions => ({
  radius,
  color: "#fff",
  weight: 3,
  fillColor: fill,
  fillOpacity: 1,
});

/** The rider (green) and the pickups they're working (amber). */
export function RiderMap({ me, pins = [], height = 200 }: { me?: { lat: number; lng: number }; pins?: MapPin[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const m = L.map(box.current, { zoomControl: false }).setView(MADINA, 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: "© OpenStreetMap",
    }).addTo(m);
    map.current = m;
    layer.current = L.layerGroup().addTo(m);
    return () => {
      m.remove();
      map.current = layer.current = null;
    };
  }, []);

  // Redraw only when a point moves, not on every render.
  const key = JSON.stringify([me?.lat, me?.lng, pins]);
  useEffect(() => {
    const m = map.current;
    const group = layer.current;
    if (!m || !group) return;
    group.clearLayers();
    const points: L.LatLngTuple[] = [];
    if (me) {
      L.circleMarker([me.lat, me.lng], dot("#1F6B45", 9)).bindTooltip("You").addTo(group);
      points.push([me.lat, me.lng]);
    }
    for (const pin of pins) {
      L.circleMarker([pin.lat, pin.lng], dot("#F2B33D", 8)).bindTooltip(pin.label).addTo(group);
      points.push([pin.lat, pin.lng]);
    }
    if (points.length > 1) m.fitBounds(L.latLngBounds(points), { padding: [32, 32], maxZoom: 16 });
    else if (points.length === 1) m.setView(points[0], 15);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const label = me
    ? `Map showing your location${pins.length ? ` and ${pins.length} pickup${pins.length > 1 ? "s" : ""}` : ""}`
    : "Map of Madina. Waiting for your location.";
  return <div ref={box} className="map" style={{ height }} role="img" aria-label={label} />;
}
