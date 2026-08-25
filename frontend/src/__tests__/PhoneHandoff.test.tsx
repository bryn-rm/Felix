import "@testing-library/jest-dom";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PhoneHandoff } from "@/components/meetings/PhoneHandoff";
import { liveAssistViewerPath } from "@/components/meetings/constants";

const EXPECTED = `http://localhost${liveAssistViewerPath("m-1")}`;

function openDialog() {
  render(<PhoneHandoff meetingId="m-1" />);
  fireEvent.click(screen.getByRole("button", { name: /view on phone/i }));
}

describe("PhoneHandoff", () => {
  it("offers the handoff without opening anything until it's asked for", () => {
    render(<PhoneHandoff meetingId="m-1" />);

    expect(screen.getByRole("button", { name: /view on phone/i })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("encodes and shows the authenticated viewer route for this meeting", () => {
    openDialog();

    // Exactly the app's own viewer path on this origin — no share token, no
    // meeting secret, nothing that would grant access by possession.
    expect(screen.getByDisplayValue(EXPECTED)).toBeInTheDocument();
    const qr = screen.getByTitle("Live Assist viewer link");
    expect(qr).toBeInTheDocument();
  });

  it("encodes the same URL it displays", () => {
    openDialog();

    // A QR nobody can read against a link everyone can is how the two drift
    // apart; both come from one helper, and this is the assertion that says so.
    const shown = (screen.getByLabelText(/or open this link/i) as HTMLInputElement).value;
    expect(shown).toBe(EXPECTED);
    expect(shown).toBe(`${window.location.origin}${liveAssistViewerPath("m-1")}`);
  });

  it("copies the link for when scanning isn't practical", async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    openDialog();

    fireEvent.click(screen.getByRole("button", { name: /copy link/i }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(EXPECTED));
    expect(await screen.findByText(/copied/i)).toBeInTheDocument();
  });

  it("claims nothing when the clipboard refuses", async () => {
    // The link is on screen and selectable either way; a false "Copied" is the
    // only outcome that would actually mislead.
    Object.assign(navigator, {
      clipboard: { writeText: jest.fn().mockRejectedValue(new Error("denied")) },
    });
    openDialog();

    fireEvent.click(screen.getByRole("button", { name: /copy link/i }));

    await waitFor(() =>
      expect(screen.queryByText(/copied/i)).not.toBeInTheDocument(),
    );
  });

  it("closes on Escape", () => {
    openDialog();

    fireEvent.keyDown(window, { key: "Escape" });

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not claim a manual-notes meeting is recording", () => {
    render(<PhoneHandoff meetingId="m-1" manualNotes />);
    fireEvent.click(screen.getByRole("button", { name: /view on phone/i }));

    expect(screen.getByText(/manual-notes meeting isn’t being recorded/i)).toBeInTheDocument();
    expect(screen.queryByText(/recording stays on this device/i)).not.toBeInTheDocument();
  });
});
