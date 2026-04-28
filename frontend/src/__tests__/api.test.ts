/**
 * Tests for src/lib/api.ts
 *
 * We mock @/lib/supabase so no real Supabase connection is needed,
 * and use jest's global fetch mock.
 */

// ---------------------------------------------------------------------------
// Mock Supabase — always returns a valid session token
// ---------------------------------------------------------------------------

jest.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: jest.fn(),
      refreshSession: jest.fn(),
    },
  },
}));

import { supabase } from "@/lib/supabase";
import { api, ApiError } from "@/lib/api";

const futureExpiry = () => Math.floor(Date.now() / 1000) + 3600;
const expiredExpiry = () => Math.floor(Date.now() / 1000) - 60;

const mockGetSession = supabase.auth.getSession as jest.Mock;
const mockRefreshSession = supabase.auth.refreshSession as jest.Mock;

// ---------------------------------------------------------------------------
// Mock global fetch
// ---------------------------------------------------------------------------

const mockFetch = jest.fn();
global.fetch = mockFetch;

function makeResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: () => Promise.resolve(body),
    body: null,
  } as unknown as Response;
}

beforeEach(() => {
  mockFetch.mockReset();
  mockGetSession.mockReset();
  mockRefreshSession.mockReset();
  mockGetSession.mockResolvedValue({
    data: {
      session: {
        access_token: "test-token-123",
        expires_at: futureExpiry(),
      },
    },
  });
  mockRefreshSession.mockResolvedValue({
    data: {
      session: {
        access_token: "refreshed-token-456",
        expires_at: futureExpiry(),
      },
    },
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("api.get", () => {
  it("injects Authorization header with Bearer token", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(200, { ok: true }));

    await api.get("/some/path");

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe(
      "Bearer test-token-123",
    );
  });

  it("returns parsed JSON on 200", async () => {
    const payload = { id: "abc", name: "Felix" };
    mockFetch.mockResolvedValueOnce(makeResponse(200, payload));

    const result = await api.get<typeof payload>("/data");

    expect(result).toEqual(payload);
  });

  it("refreshes and retries once on a 401 response", async () => {
    mockFetch
      .mockResolvedValueOnce(makeResponse(401, { detail: "Unauthorized" }))
      .mockResolvedValueOnce(makeResponse(200, { ok: true }));

    const result = await api.get<{ ok: boolean }>("/protected");

    expect(result).toEqual({ ok: true });
    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    const [, retryInit] = mockFetch.mock.calls[1] as [string, RequestInit];
    expect((retryInit.headers as Record<string, string>)["Authorization"]).toBe(
      "Bearer refreshed-token-456",
    );
  });

  it("throws ApiError with status 401 when refresh retry also fails", async () => {
    const consoleErrorSpy = jest
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    mockFetch
      .mockResolvedValueOnce(makeResponse(401, { detail: "Unauthorized" }))
      .mockResolvedValueOnce(makeResponse(401, { detail: "Unauthorized" }));

    let caught: unknown;
    try {
      await api.get("/protected");
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(401);
    consoleErrorSpy.mockRestore();
  });

  it("refreshes before the first request when the cached session is expired", async () => {
    mockGetSession.mockResolvedValueOnce({
      data: {
        session: {
          access_token: "expired-token",
          expires_at: expiredExpiry(),
        },
      },
    });
    mockFetch.mockResolvedValueOnce(makeResponse(200, { ok: true }));

    await api.get("/some/path");

    expect(mockRefreshSession).toHaveBeenCalledTimes(1);
    const [, init] = mockFetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe(
      "Bearer refreshed-token-456",
    );
  });

  it("throws ApiError with status 500 on 500 response", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(500, { detail: "Internal error" }));

    let caught: unknown;
    try {
      await api.get("/broken");
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(500);
  });
});
