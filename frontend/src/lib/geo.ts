import type { Fix } from "./api";

export function toFix(position: GeolocationPosition): Fix {
  return {
    lat: position.coords.latitude,
    lng: position.coords.longitude,
    accuracy_m: Math.round(position.coords.accuracy),
    recorded_at: new Date(position.timestamp).toISOString(),
  };
}

export function currentFix(): Promise<Fix> {
  return new Promise((resolve, reject) => {
    if (!("geolocation" in navigator)) {
      reject(new Error("This phone can't share its location."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(toFix(position)),
      (error) =>
        reject(
          new Error(
            error.code === error.PERMISSION_DENIED
              ? "Allow location access for this app in your browser settings."
              : "Couldn't get your location. Move into the open and try again.",
          ),
        ),
      { enableHighAccuracy: true, timeout: 20_000, maximumAge: 30_000 },
    );
  });
}

export function directionsUrl(lat: number, lng: number): string {
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}&travelmode=driving`;
}
