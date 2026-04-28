"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSWRConfig, type ScopedMutator } from "swr";
import { supabase } from "@/lib/supabase";
import { getFreshSession } from "@/lib/auth-session";

const RESUME_REFRESH_SKEW_SECONDS = 120;

function isExpiringSoon(expiresAt: number | undefined): boolean {
  if (!expiresAt) return false;
  return expiresAt <= Math.floor(Date.now() / 1000) + RESUME_REFRESH_SKEW_SECONDS;
}

export function clearAllSWR(mutate: ScopedMutator): Promise<unknown> {
  return mutate(() => true, undefined, { revalidate: false });
}

export function AuthSync() {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const currentUserIdRef = useRef<string | null | undefined>(undefined);
  const resumeRefreshInFlightRef = useRef(false);

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

    const refreshAfterResume = async () => {
      if (resumeRefreshInFlightRef.current) return;
      resumeRefreshInFlightRef.current = true;

      try {
        const {
          data: { session },
        } = await supabase.auth.getSession();

        if (!session?.access_token || !isExpiringSoon(session.expires_at)) {
          return;
        }

        const before = session.access_token;
        const fresh = await getFreshSession({ forceRefresh: true });
        if (fresh?.access_token && fresh.access_token !== before) {
          router.refresh();
        }
      } finally {
        resumeRefreshInFlightRef.current = false;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState !== "visible") return;
      void refreshAfterResume();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      listener.subscription.unsubscribe();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [mutate, router]);

  return null;
}
