/**
 * @jest-environment node
 *
 * `next/server` needs the platform Request/Response, which the jsdom
 * environment doesn't provide.
 */
import { NextRequest } from "next/server";

const exchangeCodeForSession = jest.fn();
jest.mock("@supabase/ssr", () => ({
  createServerClient: () => ({ auth: { exchangeCodeForSession } }),
}));

import { GET } from "@/app/auth/callback/route";
import { RETURN_TO_COOKIE } from "@/lib/return-to";

const VIEWER = "/meetings/live/m-1/viewer";

function callbackRequest(cookie?: string): NextRequest {
  return new NextRequest("https://felix.app/auth/callback?code=abc123", {
    headers: cookie ? { cookie } : undefined,
  });
}

beforeEach(() => {
  exchangeCodeForSession.mockReset();
  exchangeCodeForSession.mockResolvedValue({ error: null });
});

describe("/auth/callback — returning to the page that required sign-in", () => {
  it("forwards a stored return path on to /connect", async () => {
    // The phone half of the handoff: the viewer link was opened cold, the app
    // layout bounced to /login, and this is the hop that has to remember why.
    const res = await GET(
      callbackRequest(`${RETURN_TO_COOKIE}=${encodeURIComponent(VIEWER)}`),
    );

    expect(res.headers.get("location")).toBe(
      `https://felix.app/connect?next=${encodeURIComponent(VIEWER)}`,
    );
  });

  it("consumes the cookie so it can't steer a later sign-in", async () => {
    const res = await GET(
      callbackRequest(`${RETURN_TO_COOKIE}=${encodeURIComponent(VIEWER)}`),
    );

    const cleared = res.cookies.get(RETURN_TO_COOKIE);
    expect(cleared === undefined || cleared.value === "").toBe(true);
  });

  it("ignores an off-site cookie value instead of forwarding it", async () => {
    // A cookie is client-side state: someone can set this one by hand, so the
    // callback validates it rather than trusting that Felix wrote it.
    const res = await GET(
      callbackRequest(
        `${RETURN_TO_COOKIE}=${encodeURIComponent("https://evil.example/steal")}`,
      ),
    );

    expect(res.headers.get("location")).toBe("https://felix.app/connect");
  });

  it("still lands on /connect when nothing was stored", async () => {
    const res = await GET(callbackRequest());

    expect(res.headers.get("location")).toBe("https://felix.app/connect");
  });

  it("does not decode an already-decoded cookie value a second time", async () => {
    const pathWithPercent = "/meetings/live/m%/viewer";
    const res = await GET(
      callbackRequest(
        `${RETURN_TO_COOKIE}=${encodeURIComponent(pathWithPercent)}`,
      ),
    );

    expect(res.headers.get("location")).toBe(
      `https://felix.app/connect?next=${encodeURIComponent(pathWithPercent)}`,
    );
  });

  it("never skips the Gmail-connection check on the way", async () => {
    // The return path names a destination, not a permission: it must not turn
    // into a redirect straight past /connect.
    const res = await GET(
      callbackRequest(`${RETURN_TO_COOKIE}=${encodeURIComponent(VIEWER)}`),
    );

    expect(new URL(res.headers.get("location")!).pathname).toBe("/connect");
  });
});
