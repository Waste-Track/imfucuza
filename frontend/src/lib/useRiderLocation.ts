import { useEffect, useRef, useState } from "react";
import { api, type Fix } from "./api";
import { toFix } from "./geo";

const SEND_EVERY_MS = 30_000;
const MAX_BATCH = 100;

/**
 * While on duty, watch the phone's GPS and send fixes in batches, so dispatch
 * can find the nearest rider. Fixes taken offline are kept and sent later.
 */
export function useRiderLocation(onDuty: boolean): { problem: string | undefined } {
  const [problem, setProblem] = useState<string>();
  const pending = useRef<Fix[]>([]);

  useEffect(() => {
    if (!onDuty) return;
    if (!("geolocation" in navigator)) {
      setProblem("This phone can't share its location, so you won't get offers.");
      return;
    }

    const flush = async () => {
      if (!pending.current.length) return;
      const batch = pending.current.slice(-MAX_BATCH);
      try {
        await api.reportLocations(batch);
        pending.current = pending.current.filter((f) => !batch.includes(f));
      } catch {
        // Offline: keep them for the next try.
      }
    };

    let first = true;
    const watch = navigator.geolocation.watchPosition(
      (position) => {
        setProblem(undefined);
        pending.current.push(toFix(position));
        if (first) {
          first = false;
          void flush();
        }
      },
      (error) =>
        setProblem(
          error.code === error.PERMISSION_DENIED
            ? "Allow location for this app, or you won't get offers."
            : "Waiting for GPS…",
        ),
      { enableHighAccuracy: true, maximumAge: 15_000 },
    );
    const timer = setInterval(() => void flush(), SEND_EVERY_MS);
    return () => {
      navigator.geolocation.clearWatch(watch);
      clearInterval(timer);
      void flush();
    };
  }, [onDuty]);

  return { problem: onDuty ? problem : undefined };
}
