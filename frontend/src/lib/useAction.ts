import { useState } from "react";
import { ApiError } from "./api";

/** Run one button's action: tracks busy, keeps the error to show, then calls `onDone`. */
export function useAction(onDone?: () => void) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const run = async (action: () => Promise<unknown>): Promise<boolean> => {
    setBusy(true);
    setError(undefined);
    try {
      await action();
      onDone?.();
      return true;
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "That didn't work.");
      return false;
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, run, setError };
}
