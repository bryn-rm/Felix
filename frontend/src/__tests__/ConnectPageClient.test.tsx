import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mockGet = jest.fn((_path: string) => new Promise(() => {}));
jest.mock("@/lib/api", () => ({
  api: { get: (path: string) => mockGet(path) },
}));

import { ConnectPageClient } from "@/app/(auth)/connect/page-client";

describe("ConnectPageClient", () => {
  beforeEach(() => mockGet.mockClear());

  it("carries the validated destination into the Gmail OAuth request", async () => {
    render(
      <ConnectPageClient
        initialError={null}
        returnTo="/meetings/live/m-1/viewer?from=phone"
      />,
    );

    fireEvent.click(
      screen.getByRole("button", { name: /connect gmail & calendar/i }),
    );

    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith(
        "/auth/google/connect?next=%2Fmeetings%2Flive%2Fm-1%2Fviewer%3Ffrom%3Dphone",
      ),
    );
  });

  it("uses the original endpoint when there is no destination", async () => {
    render(<ConnectPageClient initialError={null} returnTo={null} />);

    fireEvent.click(
      screen.getByRole("button", { name: /connect gmail & calendar/i }),
    );

    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith("/auth/google/connect"),
    );
  });
});
