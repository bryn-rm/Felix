import {
  RETURN_TO_MAX_LENGTH,
  RETURN_TO_COOKIE,
  loginUrlFor,
  rememberReturnPath,
  safeReturnPath,
} from "@/lib/return-to";

const VIEWER = "/meetings/live/9f2c/viewer";

describe("safeReturnPath", () => {
  it("accepts the in-app path the phone handoff sends people to", () => {
    expect(safeReturnPath(VIEWER)).toBe(VIEWER);
  });

  it("keeps the query string and drops the fragment", () => {
    expect(safeReturnPath("/meetings?filter=live#top")).toBe(
      "/meetings?filter=live",
    );
  });

  it("refuses every off-site destination", () => {
    // The whole point of validating: a ?next= is attacker-supplied, and a
    // sign-in page that forwards to wherever it is told is an open redirect.
    for (const hostile of [
      "https://evil.example/steal",
      "http://evil.example",
      "//evil.example",
      "/\\evil.example",
      "/\\/\\evil.example",
      "javascript:alert(1)",
      "data:text/html,<script>alert(1)</script>",
      "meetings/live/9f2c/viewer", // relative — could resolve anywhere
      "",
      null,
      undefined,
    ]) {
      expect(safeReturnPath(hostile as string)).toBeNull();
    }
  });

  it("refuses control characters", () => {
    expect(safeReturnPath("/meetings\r\nSet-Cookie: a=b")).toBeNull();
    expect(safeReturnPath("/meetings\u0000")).toBeNull();
  });

  it("uses the same 2048-character bound as the backend validator", () => {
    expect(safeReturnPath(`/${"a".repeat(RETURN_TO_MAX_LENGTH - 1)}`)).not.toBeNull();
    expect(safeReturnPath(`/${"a".repeat(RETURN_TO_MAX_LENGTH)}`)).toBeNull();
  });
});

describe("loginUrlFor", () => {
  it("attaches a validated return path", () => {
    expect(loginUrlFor(VIEWER)).toBe(
      `/login?next=${encodeURIComponent(VIEWER)}`,
    );
  });

  it("falls back to a bare /login rather than forwarding a hostile one", () => {
    expect(loginUrlFor("https://evil.example")).toBe("/login");
    expect(loginUrlFor(null)).toBe("/login");
  });
});

describe("rememberReturnPath", () => {
  beforeEach(() => {
    document.cookie = `${RETURN_TO_COOKIE}=; Path=/; Max-Age=0`;
  });

  it("stores a valid path for the OAuth round trip", () => {
    rememberReturnPath(VIEWER);
    expect(document.cookie).toContain(
      `${RETURN_TO_COOKIE}=${encodeURIComponent(VIEWER)}`,
    );
  });

  it("stores nothing for a hostile path", () => {
    rememberReturnPath("//evil.example");
    expect(document.cookie).not.toContain(RETURN_TO_COOKIE);
  });

  it("clears a stale destination when this sign-in has no valid path", () => {
    rememberReturnPath(VIEWER);
    expect(document.cookie).toContain(RETURN_TO_COOKIE);

    rememberReturnPath(null);
    expect(document.cookie).not.toContain(RETURN_TO_COOKIE);

    rememberReturnPath(VIEWER);
    rememberReturnPath("//evil.example");
    expect(document.cookie).not.toContain(RETURN_TO_COOKIE);
  });
});
