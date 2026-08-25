import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

let pathname = "/home";
jest.mock("next/navigation", () => ({ usePathname: () => pathname }));

const openModal = jest.fn();
let modalOpen = false;
jest.mock("@/components/felix/VoiceContext", () => ({
  useVoiceContext: () => ({ state: "idle", openModal, modalOpen }),
}));
jest.mock("@/components/felix/VoiceOrb", () => ({
  VoiceOrb: () => <button aria-label="Voice orb" />,
}));

import { FloatingVoiceFab } from "@/components/felix/FloatingVoiceFab";

beforeEach(() => {
  modalOpen = false;
  pathname = "/home";
});

describe("FloatingVoiceFab", () => {
  it("floats over ordinary pages", () => {
    render(<FloatingVoiceFab />);
    expect(screen.getByLabelText("Voice orb")).toBeInTheDocument();
  });

  it("stays out of the Live Assist viewer", () => {
    // On a phone it lands exactly on that page's ask button, and tapping it
    // would open a microphone session on a device deliberately kept out of the
    // capture path.
    pathname = "/meetings/live/m-1/viewer";
    render(<FloatingVoiceFab />);
    expect(screen.queryByLabelText("Voice orb")).not.toBeInTheDocument();
  });

  it("still appears on the capturing device's own live page", () => {
    pathname = "/meetings/live/m-1";
    render(<FloatingVoiceFab />);
    expect(screen.getByLabelText("Voice orb")).toBeInTheDocument();
  });
});
