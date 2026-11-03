import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";

const mockGet = jest.fn();
const mockPush = jest.fn();
jest.mock("@/lib/api", () => ({ api: { get: (...args: unknown[]) => mockGet(...args) } }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: mockPush }) }));
jest.mock("@/lib/supabase", () => ({ supabase: { auth: { signOut: jest.fn() } } }));

import { GoogleDisconnectedBanner } from "@/components/layout/GoogleDisconnectedBanner";
import { clearGoogleDisconnected, markGoogleDisconnected } from "@/lib/google-connection-status";

const originalLocation = window.location;
beforeEach(() => {
  AbortSignal.timeout = jest.fn(() => new AbortController().signal);
  jest.clearAllMocks();
  clearGoogleDisconnected();
  markGoogleDisconnected();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname: "/settings", search: "?tab=google", href: "" },
  });
  mockGet.mockImplementation((path: string) => path === "/auth/google/status"
    ? Promise.resolve({ connected: false, needs_reconnect: true })
    : Promise.resolve({ auth_url: "https://accounts.google.com/consent" }));
});
afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: originalLocation });
  clearGoogleDisconnected();
});

function showBanner() {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <GoogleDisconnectedBanner />
    </SWRConfig>,
  );
}

it("starts Google consent directly and carries the current page", async () => {
  showBanner();
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Google" }));
  await waitFor(() => expect(window.location.href).toBe("https://accounts.google.com/consent"));
  expect(mockGet).toHaveBeenCalledWith(
    "/auth/google/connect?next=%2Fsettings%3Ftab%3Dgoogle", expect.any(Object),
  );
  expect(mockPush).not.toHaveBeenCalled();
});

it("shows request errors and allows another attempt", async () => {
  mockGet.mockImplementation((path: string) => path === "/auth/google/status"
    ? Promise.resolve({ connected: false, needs_reconnect: true })
    : Promise.reject(new Error("Could not reach Google")));
  showBanner();
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Google" }));
  expect(await screen.findByText("Could not reach Google")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reconnect Google" })).toBeEnabled();
});

it("prevents duplicate starts while the authorization URL is loading", async () => {
  mockGet.mockImplementation((path: string) => path === "/auth/google/status"
    ? Promise.resolve({ connected: false, needs_reconnect: true })
    : new Promise(() => {}));
  showBanner();
  fireEvent.click(screen.getByRole("button", { name: "Reconnect Google" }));
  expect(screen.getByRole("button", { name: /redirecting to google/i })).toBeDisabled();
});
