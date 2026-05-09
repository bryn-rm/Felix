"use client";

import { useEffect, useState } from "react";
import { supabase } from "@/lib/supabase";
import { inboxDebug } from "@/lib/inbox-debug";

/**
 * Returns `true` once the Supabase browser client has loaded its session
 * for the current page (or confirmed there is none). Use this to gate SWR
 * hooks so they don't fire before the access token is available — without
 * the gate, the very first fetch after hydration can throw 401 in
 * `getAuthHeader` and SWR will cache that error until the key changes.
 */
export function useSessionReady(): boolean {
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;

    supabase.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      inboxDebug("session-ready:initial", {
        hasSession: !!data.session?.access_token,
      });
      setReady(true);
    });

    const { data: listener } = supabase.auth.onAuthStateChange(
      (event, session) => {
        inboxDebug("session-ready:auth-event", {
          event,
          hasSession: !!session?.access_token,
        });
        if (session?.access_token || event === "INITIAL_SESSION") {
          setReady(true);
        }
      },
    );

    return () => {
      cancelled = true;
      listener.subscription.unsubscribe();
    };
  }, []);

  return ready;
}
