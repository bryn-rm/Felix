/**
 * The unauthenticated phone entry flow, end to end through the server-side
 * hops: open a Live Assist viewer link cold → sign in → come back to that
 * meeting. Ownership is never part of it — the return path only ever names a
 * destination.
 */
const getUser = jest.fn();
const getSession = jest.fn();
jest.mock("@supabase/ssr", () => ({
  createServerClient: () => ({ auth: { getUser, getSession } }),
  // AppShell pulls in the browser client transitively; it plays no part here.
  createBrowserClient: () => ({ auth: {} }),
}));

const headerStore = new Map<string, string>();
jest.mock("next/headers", () => ({
  cookies: () => ({ getAll: () => [], set: jest.fn() }),
  headers: () => ({ get: (name: string) => headerStore.get(name) ?? null }),
}));

// redirect() throws in Next so nothing downstream runs; mirror that, and carry
// the destination on the thrown value so a test can read it.
class RedirectError extends Error {
  constructor(public readonly to: string) {
    super(`NEXT_REDIRECT:${to}`);
  }
}
jest.mock("next/navigation", () => ({
  redirect: (to: string) => {
    throw new RedirectError(to);
  },
}));

import AppLayout from "@/app/(app)/layout";
import ConnectPage from "@/app/(auth)/connect/page";
import { PATHNAME_HEADER } from "@/lib/return-to";

const VIEWER = "/meetings/live/m-1/viewer";

async function redirectFrom(run: () => Promise<unknown>): Promise<string> {
  try {
    await run();
  } catch (e) {
    if (e instanceof RedirectError) return e.to;
    throw e;
  }
  throw new Error("expected a redirect");
}

beforeEach(() => {
  getUser.mockReset();
  getSession.mockReset();
  headerStore.clear();
  global.fetch = jest.fn() as unknown as typeof fetch;
});

describe("app layout — sending an unauthenticated visitor to sign in", () => {
  beforeEach(() => {
    getUser.mockResolvedValue({ data: { user: null } });
  });

  it("remembers the viewer URL the phone was opened on", async () => {
    headerStore.set(PATHNAME_HEADER, VIEWER);

    expect(
      await redirectFrom(() => AppLayout({ children: null })),
    ).toBe(`/login?next=${encodeURIComponent(VIEWER)}`);
  });

  it("refuses to carry an off-site path, even one forged into the header", async () => {
    // The middleware sets this header with `set`, so a client-sent one is
    // already overwritten — this is the second line of defence, not the first.
    headerStore.set(PATHNAME_HEADER, "https://evil.example/steal");

    expect(await redirectFrom(() => AppLayout({ children: null }))).toBe("/login");
  });

  it("falls back to a bare /login when there is no path to return to", async () => {
    expect(await redirectFrom(() => AppLayout({ children: null }))).toBe("/login");
  });
});

describe("app layout — sending a disconnected user to Google connect", () => {
  beforeEach(() => {
    getUser.mockResolvedValue({ data: { user: { id: "u-1" } } });
    getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ connected: false }),
    });
  });

  it("preserves the viewer URL through the connection guard", async () => {
    headerStore.set(PATHNAME_HEADER, VIEWER);

    expect(await redirectFrom(() => AppLayout({ children: null }))).toBe(
      `/connect?next=${encodeURIComponent(VIEWER)}`,
    );
  });
});

describe("connect page — the last hop back to the viewer", () => {
  beforeEach(() => {
    getUser.mockResolvedValue({ data: { user: { id: "u-1" } } });
    getSession.mockResolvedValue({ data: { session: { access_token: "t" } } });
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ connected: true }),
    });
  });

  it("lands the signed-in phone on the meeting it was opened for", async () => {
    const to = await redirectFrom(() =>
      ConnectPage({ searchParams: Promise.resolve({ next: VIEWER }) }),
    );

    expect(to).toBe(VIEWER);
  });

  it("sends an off-site next to /home instead", async () => {
    const to = await redirectFrom(() =>
      ConnectPage({
        searchParams: Promise.resolve({ next: "https://evil.example/steal" }),
      }),
    );

    expect(to).toBe("/home");
  });

  it("keeps its usual /home destination when nothing was requested", async () => {
    const to = await redirectFrom(() =>
      ConnectPage({ searchParams: Promise.resolve({}) }),
    );

    expect(to).toBe("/home");
  });

  it("preserves the viewer URL when the Supabase session is missing", async () => {
    getUser.mockResolvedValue({ data: { user: null } });

    expect(
      await redirectFrom(() =>
        ConnectPage({ searchParams: Promise.resolve({ next: VIEWER }) }),
      ),
    ).toBe(`/login?next=${encodeURIComponent(VIEWER)}`);
  });

  it("does not honour a return path when Gmail isn't connected yet", async () => {
    // Ordering that matters: the return path must not become a way around the
    // connection gate. Nothing redirects — the connect prompt renders.
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ connected: false }),
    });

    const page = await ConnectPage({
      searchParams: Promise.resolve({ next: VIEWER }),
    });

    expect(page).toBeTruthy();
    expect(page.props.returnTo).toBe(VIEWER);
  });

  it("drops an unsafe return path before rendering the connection client", async () => {
    (global.fetch as jest.Mock).mockResolvedValue({
      ok: true,
      json: async () => ({ connected: false }),
    });

    const page = await ConnectPage({
      searchParams: Promise.resolve({ next: "https://evil.example/steal" }),
    });

    expect(page.props.returnTo).toBeNull();
  });
});
