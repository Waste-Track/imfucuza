import type { Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "./lib/api";
import { supabase } from "./lib/supabase";
import type { Me } from "./lib/types";

type SessionState = {
  // undefined while the stored session is being read.
  session: Session | null | undefined;
  me: Me | undefined;
  meError: ApiError | undefined;
  refreshMe: () => Promise<Me | undefined>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [me, setMe] = useState<Me | undefined>(undefined);
  const [meError, setMeError] = useState<ApiError | undefined>(undefined);

  useEffect(() => {
    void supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);

  const refreshMe = useCallback(async () => {
    try {
      const next = await api.me();
      setMe(next);
      setMeError(undefined);
      return next;
    } catch (error) {
      setMeError(error instanceof ApiError ? error : new ApiError(0, {}));
      return undefined;
    }
  }, []);

  const userId = session?.user.id;
  useEffect(() => {
    if (userId) void refreshMe();
    else setMe(undefined);
  }, [userId, refreshMe]);

  const signOut = useCallback(async () => {
    await supabase.auth.signOut();
    setMe(undefined);
  }, []);

  return (
    <SessionContext.Provider value={{ session, me, meError, refreshMe, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession outside SessionProvider");
  return value;
}

/** For screens that only render once `me` has loaded. */
export function useMe(): Me {
  const { me } = useSession();
  if (!me) throw new Error("useMe before /v1/me loaded");
  return me;
}
