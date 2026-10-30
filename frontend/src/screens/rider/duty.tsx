import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Fix } from "../../lib/api";
import { useRiderLocation } from "../../lib/useRiderLocation";
import { useMe, useSession } from "../../session";

type Duty = {
  onDuty: boolean;
  switching: boolean;
  error: string | undefined;
  // Location problems, such as permission refused.
  problem: string | undefined;
  position: Fix | undefined;
  setOnDuty: (next: boolean) => Promise<void>;
};

const DutyContext = createContext<Duty | null>(null);

// A break lasts until the app is closed: the next visit starts online again.
const BREAK_KEY = "imfucuza.rider-break";

function onBreak(): boolean {
  try {
    return sessionStorage.getItem(BREAK_KEY) === "1";
  } catch {
    return false;
  }
}

function rememberBreak(taking: boolean): void {
  try {
    if (taking) sessionStorage.setItem(BREAK_KEY, "1");
    else sessionStorage.removeItem(BREAK_KEY);
  } catch {
    // ignore
  }
}

/**
 * Opening the app puts the rider online, and their location is shared on every
 * rider screen while they are, so dispatch can find them mid-job too.
 */
export function RiderDutyProvider({ children }: { children: ReactNode }) {
  const me = useMe();
  const { refreshMe } = useSession();
  const [onDuty, setOnDutyState] = useState(Boolean(me.on_duty));
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string>();
  const { problem, position } = useRiderLocation(onDuty);

  const setOnDuty = useCallback(
    async (next: boolean) => {
      setSwitching(true);
      setError(undefined);
      try {
        const result = await api.setDuty(next);
        setOnDutyState(result.on_duty);
        rememberBreak(!result.on_duty);
        void refreshMe();
      } catch (e) {
        setError(e instanceof ApiError ? e.message : "Couldn't change your status.");
      } finally {
        setSwitching(false);
      }
    },
    [refreshMe],
  );

  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!me.on_duty && !onBreak()) void setOnDuty(true);
  }, [me.on_duty, setOnDuty]);

  // Dispatch takes a rider off duty after missed offers: follow the Engine.
  useEffect(() => {
    if (!switching) setOnDutyState(Boolean(me.on_duty));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me.on_duty]);
  useEffect(() => {
    const timer = setInterval(() => void refreshMe(), 60_000);
    return () => clearInterval(timer);
  }, [refreshMe]);

  return (
    <DutyContext.Provider value={{ onDuty, switching, error, problem, position, setOnDuty }}>
      {children}
    </DutyContext.Provider>
  );
}

export function useDuty(): Duty {
  const value = useContext(DutyContext);
  if (!value) throw new Error("useDuty outside RiderDutyProvider");
  return value;
}
