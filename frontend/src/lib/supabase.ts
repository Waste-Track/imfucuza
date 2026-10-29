import { createClient } from "@supabase/supabase-js";

const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!url || !key) {
  console.error("Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY (see .env.example).");
}

export const supabase = createClient(url ?? "http://127.0.0.1:54321", key ?? "missing", {
  auth: { persistSession: true, autoRefreshToken: true },
});
