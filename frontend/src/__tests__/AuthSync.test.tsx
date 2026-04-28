/**
 * Tests for AuthSync — specifically the visibilitychange resume path that
 * refreshes an expiring session when the user returns to a backgrounded tab.
 */
import "@testing-library/jest-dom";
import { act, render } from "@testing-library/react";

const mockRouterRefresh = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: mockRouterRefresh }),
}));

jest.mock("swr", () => ({
  useSWRConfig: () => ({ mutate: jest.fn() }),
}));

const mockGetSession: jest.Mock = jest.fn();
const mockOnAuthStateChange: jest.Mock = jest.fn(() => ({
  data: { subscription: { unsubscribe: jest.fn() } },
}));

jest.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: () => mockGetSession(),
      onAuthStateChange: (cb: unknown) => mockOnAuthStateChange(cb),
    },
  },
}));

const mockGetFreshSession: jest.Mock = jest.fn();
jest.mock("@/lib/auth-session", () => ({
  getFreshSession: (options?: unknown) => mockGetFreshSession(options),
}));

import { AuthSync } from "@/components/auth/AuthSync";

const futureExpiry = () => Math.floor(Date.now() / 1000) + 3600;
const expiringExpiry = () => Math.floor(Date.now() / 1000) + 30;

beforeEach(() => {
  mockRouterRefresh.mockClear();
  mockGetFreshSession.mockReset();
  mockGetSession.mockReset();
  mockGetSession.mockResolvedValue({
    data: {
      session: {
        access_token: "stale-token",
        expires_at: futureExpiry(),
        user: { id: "u1" },
      },
    },
  });
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

function fireVisibilityChange(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event("visibilitychange"));
}

describe("AuthSync visibility resume", () => {
  it("does not refresh when the visible tab has a healthy cached session", async () => {
    mockGetFreshSession.mockResolvedValue({ access_token: "stale-token" });

    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("visible");
      await flush();
    });

    expect(mockGetFreshSession).not.toHaveBeenCalled();
    expect(mockRouterRefresh).not.toHaveBeenCalled();
  });

  it("force-refreshes the session when the visible tab has an expiring token", async () => {
    mockGetSession.mockResolvedValue({
      data: {
        session: {
          access_token: "stale-token",
          expires_at: expiringExpiry(),
          user: { id: "u1" },
        },
      },
    });
    mockGetFreshSession.mockResolvedValue({ access_token: "stale-token" });

    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("visible");
      await flush();
    });

    expect(mockGetFreshSession).toHaveBeenCalledWith({ forceRefresh: true });
  });

  it("calls router.refresh() when the resume produces a new access_token", async () => {
    mockGetSession.mockResolvedValue({
      data: {
        session: {
          access_token: "stale-token",
          expires_at: expiringExpiry(),
          user: { id: "u1" },
        },
      },
    });
    mockGetFreshSession.mockResolvedValue({ access_token: "fresh-token" });

    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("visible");
      await flush();
    });

    expect(mockRouterRefresh).toHaveBeenCalledTimes(1);
  });

  it("skips router.refresh() when the access_token is unchanged", async () => {
    mockGetSession.mockResolvedValue({
      data: {
        session: {
          access_token: "stale-token",
          expires_at: expiringExpiry(),
          user: { id: "u1" },
        },
      },
    });
    mockGetFreshSession.mockResolvedValue({ access_token: "stale-token" });

    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("visible");
      await flush();
    });

    expect(mockRouterRefresh).not.toHaveBeenCalled();
  });

  it("does not refresh or router.refresh when there is no cached session", async () => {
    mockGetSession.mockResolvedValue({
      data: { session: null },
    });

    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("visible");
      await flush();
    });

    expect(mockGetFreshSession).not.toHaveBeenCalled();
    expect(mockRouterRefresh).not.toHaveBeenCalled();
  });

  it("ignores visibilitychange to hidden", async () => {
    render(<AuthSync />);
    await flush();

    await act(async () => {
      fireVisibilityChange("hidden");
      await flush();
    });

    expect(mockGetFreshSession).not.toHaveBeenCalled();
  });

  it("deduplicates rapid visible events while a resume check is in flight", async () => {
    let resolveGetSession: (value: unknown) => void = () => undefined;
    mockGetSession.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveGetSession = resolve;
        }),
    );

    render(<AuthSync />);

    await act(async () => {
      fireVisibilityChange("visible");
      fireVisibilityChange("visible");
      resolveGetSession({
        data: {
          session: {
            access_token: "stale-token",
            expires_at: expiringExpiry(),
            user: { id: "u1" },
          },
        },
      });
      await flush();
    });

    expect(mockGetFreshSession).toHaveBeenCalledTimes(1);
  });
});
