// What a new visitor chose on the landing page, kept across the SMS code step
// so they land on the right registration form. Storage can be unavailable
// (private windows), in which case they simply choose again.

export type Intent = "household" | "rider";

const KEY = "imfucuza.intent";

export function saveIntent(intent: Intent | undefined): void {
  try {
    if (intent) localStorage.setItem(KEY, intent);
    else localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
}

export function loadIntent(): Intent | undefined {
  try {
    const value = localStorage.getItem(KEY);
    return value === "household" || value === "rider" ? value : undefined;
  } catch {
    return undefined;
  }
}
