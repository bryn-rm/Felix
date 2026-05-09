const enabled =
  typeof window !== "undefined" &&
  (process.env.NEXT_PUBLIC_INBOX_DEBUG === "1" ||
    process.env.NODE_ENV !== "production");

export function inboxDebug(event: string, data?: unknown) {
  if (!enabled) return;
  const ts =
    typeof performance !== "undefined" ? performance.now().toFixed(0) : "0";
  // eslint-disable-next-line no-console
  console.log(`[inbox-debug] ${ts}ms ${event}`, data ?? "");
}
