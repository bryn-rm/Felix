/**
 * @jest-environment node
 *
 * `next/server` needs the platform Request/Response, which jsdom doesn't
 * provide.
 */
import { NextRequest } from "next/server";

// Whether the Supabase client refreshes the session decides which of the
// middleware's two response constructions runs, so the test drives it.
let refreshedCookies: { name: string; value: string; options?: object }[] = [];
jest.mock("@supabase/ssr", () => ({
  createServerClient: (
    _url: string,
    _key: string,
    options: { cookies: { setAll: (c: unknown[]) => void } },
  ) => ({
    auth: {
      getUser: async () => {
        if (refreshedCookies.length) options.cookies.setAll(refreshedCookies);
        return { data: { user: null } };
      },
    },
  }),
}));

import { middleware } from "@/middleware";
import { PATHNAME_HEADER } from "@/lib/return-to";

const VIEWER = "/meetings/live/m-1/viewer";

/**
 * The header only exists as an internal request header on the response Next
 * forwards, so it is read back the way Next transports it.
 */
function forwardedHeader(res: Response, name: string): string | null {
  const overridden = res.headers.get("x-middleware-override-headers");
  if (!overridden?.split(",").map((h) => h.trim()).includes(name)) return null;
  return res.headers.get(`x-middleware-request-${name}`);
}

beforeEach(() => {
  refreshedCookies = [];
});

describe("middleware — stamping the path for the auth guard", () => {
  it("passes the requested path down to the Server Component that renders it", async () => {
    const res = await middleware(
      new NextRequest(`https://felix.app${VIEWER}`),
    );

    expect(forwardedHeader(res, PATHNAME_HEADER)).toBe(VIEWER);
  });

  it("keeps the query string, which is part of where the user was going", async () => {
    const res = await middleware(
      new NextRequest("https://felix.app/meetings?filter=live"),
    );

    expect(forwardedHeader(res, PATHNAME_HEADER)).toBe("/meetings?filter=live");
  });

  it("overwrites a client-supplied header of the same name", async () => {
    // Otherwise a crafted request could pick its own post-login destination.
    // It would still have to survive validation, but it must not get this far.
    const res = await middleware(
      new NextRequest(`https://felix.app${VIEWER}`, {
        headers: { [PATHNAME_HEADER]: "https://evil.example/steal" },
      }),
    );

    expect(forwardedHeader(res, PATHNAME_HEADER)).toBe(VIEWER);
  });

  it("keeps stamping it when the session is refreshed mid-request", async () => {
    // Refreshing rebuilds the response, and the rebuilt one is the only one the
    // Server Component ever sees. It has to carry both the refreshed cookie and
    // the path.
    refreshedCookies = [{ name: "sb-access-token", value: "fresh", options: {} }];

    const res = await middleware(new NextRequest(`https://felix.app${VIEWER}`));

    expect(forwardedHeader(res, PATHNAME_HEADER)).toBe(VIEWER);
    expect(res.cookies.get("sb-access-token")?.value).toBe("fresh");
  });
});
