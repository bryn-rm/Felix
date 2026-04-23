"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSWRConfig, type ScopedMutator } from "swr";
import { supabase } from "@/lib/supabase";

export function clearAllSWR(mutate: ScopedMutator): Promise<unknown> {
  return mutate(() => true, undefined, { revalidate: false });
}

export function AuthSync() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const currentUserIdRef = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (currentUserIdRef.current === undefined) {
        currentUserIdRef.current = session?.user?.id ?? null;
      }
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, session) => {
      const nextId = session?.user?.id ?? null;
      const prevId = currentUserIdRef.current;

      if (prevId === undefined) {
        currentUserIdRef.current = nextId;
        return;
      }

      if (prevId !== nextId) {
        currentUserIdRef.current = nextId;
        void clearAllSWR(mutate);
        router.refresh();
      }
    });

    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      // Browsers throttle background timers, so Supabase's silent
      // refresh often misses while the tab is hidden. Force one on
      // return so the next API call carries a fresh access token.
      void supabase.auth.refreshSession().catch(() => {});
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      listener.subscription.unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [mutate, router]);

  return null;
}
