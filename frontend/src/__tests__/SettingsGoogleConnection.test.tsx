import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

let mockGoogleStatus = { connected: false, needs_reconnect: true, google_email: "me@example.com" };
jest.mock("swr", () => ({
  __esModule: true,
  default: (key: string) => ({
    data: key === "/auth/google/status" ? mockGoogleStatus : undefined,
    isLoading: false,
    mutate: jest.fn(),
  }),
  useSWRConfig: () => ({ mutate: jest.fn() }),
}));
jest.mock("next/navigation", () => ({ useRouter: () => ({ push: jest.fn() }) }));
jest.mock("@/lib/supabase", () => ({
  supabase: { auth: { getUser: async () => ({ data: { user: { email: "me@example.com" } } }) } },
}));

import SettingsPage from "@/app/(app)/settings/page";
import { clearGoogleDisconnected, markGoogleDisconnected } from "@/lib/google-connection-status";

beforeEach(() => clearGoogleDisconnected());
afterEach(() => clearGoogleDisconnected());

it("shows persisted expiry with reconnect and keeps disconnect available", async () => {
  mockGoogleStatus = { connected: false, needs_reconnect: true, google_email: "me@example.com" };
  render(<SettingsPage />);
  expect(await screen.findByText(/Google access expired/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reconnect Google" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Disconnect" })).toBeEnabled();
  expect(screen.queryByText("Connected")).not.toBeInTheDocument();
});

it("shows a newly detected expiry even before the status cache revalidates", async () => {
  mockGoogleStatus = { connected: true, needs_reconnect: false, google_email: "me@example.com" };
  markGoogleDisconnected();
  render(<SettingsPage />);
  expect(await screen.findByText(/Google access expired/)).toBeInTheDocument();
  expect(screen.queryByText("Connected")).not.toBeInTheDocument();
});

it("shows a healthy grant as connected", async () => {
  mockGoogleStatus = { connected: true, needs_reconnect: false, google_email: "me@example.com" };
  render(<SettingsPage />);
  expect(await screen.findByText("Connected")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Reconnect Google" })).not.toBeInTheDocument();
});
