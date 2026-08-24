import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

import { MeetingList } from "@/components/meetings/MeetingList";
import { isMeetingCaptureSupported } from "@/lib/capture-support";
import type { Meeting } from "@/lib/types";

jest.mock("@/lib/capture-support", () => ({
  isMeetingCaptureSupported: jest.fn(),
}));

const mockSupported = isMeetingCaptureSupported as jest.Mock;

function meeting(overrides: Partial<Meeting> = {}): Meeting {
  return {
    id: "m-1",
    calendar_event_id: null,
    title: "Roadmap",
    attendees: [],
    date: "2026-08-23T09:55:00Z",
    template: "general",
    meeting_type: "general",
    user_role: null,
    status: "recording",
    source: "browser_capture",
    user_notes: null,
    started_at: "2026-08-23T09:55:00Z",
    ended_at: null,
    created_at: "2026-08-23T09:55:00Z",
    updated_at: null,
    ...overrides,
  };
}

function hrefOf(m: Meeting): string {
  render(<MeetingList meetings={[m]} onDelete={jest.fn()} />);
  return screen.getByRole("link", { name: /roadmap/i }).getAttribute("href") ?? "";
}

beforeEach(() => {
  mockSupported.mockReset();
});

describe("MeetingList routing by capture capability", () => {
  it("sends a capture-capable client to the capture page (unchanged)", () => {
    // The laptop's normal path, including resuming after a mid-meeting refresh.
    mockSupported.mockReturnValue(true);
    expect(hrefOf(meeting())).toBe("/meetings/live/m-1");
  });

  it("sends a client that cannot capture straight to the viewer", () => {
    // Phase 1's friction: a phone landed on the capture page, saw a disabled
    // Start button, and had to click through to the only surface it can use.
    mockSupported.mockReturnValue(false);
    expect(hrefOf(meeting())).toBe("/meetings/live/m-1/viewer");
  });

  it("routes on capability, not on the device being a phone", () => {
    // A desktop browser without tab-audio sharing is an observer too — the
    // check is the same capability gate the capture page itself uses.
    mockSupported.mockReturnValue(false);
    expect(hrefOf(meeting({ meeting_type: "interview", user_role: "candidate" })))
      .toBe("/meetings/live/m-1/viewer");
    expect(mockSupported).toHaveBeenCalled();
  });

  it("keeps a manual session on the live page for every client", () => {
    // A manual session has no capture step to be incapable of.
    mockSupported.mockReturnValue(false);
    expect(hrefOf(meeting({ source: "manual_notes" }))).toBe("/meetings/live/m-1");
  });

  it("sends a finished meeting to its summary regardless of capability", () => {
    mockSupported.mockReturnValue(false);
    expect(hrefOf(meeting({ status: "done" }))).toBe("/meetings/m-1");
  });
});
