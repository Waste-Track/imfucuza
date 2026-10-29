import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

type State<T> = { data: T | undefined; error: ApiError | undefined; loading: boolean };

/**
 * Load now, then again every `everyMs` while the page is visible. Pass
 * `everyMs: 0` to load once. `reload` loads straight away, e.g. after an action.
 */
export function usePoll<T>(load: () => Promise<T>, everyMs: number, deps: unknown[] = []) {
  const [state, setState] = useState<State<T>>({
    data: undefined,
    error: undefined,
    loading: true,
  });
  const loadRef = useRef(load);
  loadRef.current = load;

  const reload = useCallback(async () => {
    try {
      const data = await loadRef.current();
      setState({ data, error: undefined, loading: false });
    } catch (error) {
      // Keep the last good data on screen; offline is normal for riders.
      setState((s) => ({
        ...s,
        error: error instanceof ApiError ? error : new ApiError(0, {}),
        loading: false,
      }));
    }
  }, []);

  useEffect(() => {
    void reload();
    if (!everyMs) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void reload();
    }, everyMs);
    const onVisible = () => document.visibilityState === "visible" && void reload();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, everyMs, ...deps]);

  return { ...state, reload };
}
