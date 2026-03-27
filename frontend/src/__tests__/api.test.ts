/**
 * Tests for src/lib/api.ts
 *
 * We mock @/lib/supabase so no real Supabase connection is needed,
 * and use jest's global fetch mock.
 */

import { api, ApiError } from "@/lib/api";

// ---------------------------------------------------------------------------
// Mock Supabase — always returns a valid session token
// ---------------------------------------------------------------------------

jest.mock("@/lib/supabase", () => ({
  supabase: {
    auth: {
      getSession: jest.fn().mockResolvedValue({
        data: { session: { access_token: "test-token-123" } },
      }),
    },
  },
}));

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

  it("throws ApiError with status 401 on 401 response", async () => {
    mockFetch.mockResolvedValueOnce(makeResponse(401, { detail: "Unauthorized" }));

    let caught: unknown;
    try {
      await api.get("/protected");
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(401);
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
