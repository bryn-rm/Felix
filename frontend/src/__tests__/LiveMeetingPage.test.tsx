import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";

import LiveMeetingPage from "@/app/(app)/meetings/live/[id]/page";
import { ApiError } from "@/lib/api";
import { useMeetingCapture } from "@/hooks/useMeetingCapture";
import { useMeeting, useMeetings } from "@/hooks/useMeetings";

const push = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

jest.mock("@/hooks/useMeetingCapture", () => ({
  isMeetingCaptureSupported: () => true,
  useMeetingCapture: jest.fn(),
}));
jest.mock("@/hooks/useMeetings", () => ({
  useMeeting: jest.fn(),
  useMeetings: jest.fn(),
}));

const mockUseMeetingCapture = useMeetingCapture as jest.Mock;
const mockUseMeeting = useMeeting as jest.Mock;
const mockUseMeetings = useMeetings as jest.Mock;

let failCapture: jest.Mock;
let stop: jest.Mock;
let endMeeting: jest.Mock;

function setup(endMeetingImpl: () => Promise<unknown>) {
  failCapture = jest.fn();
  stop = jest.fn().mockResolvedValue(undefined);
  endMeeting = jest.fn().mockImplementation(endMeetingImpl);

  mockUseMeetingCapture.mockReturnValue({
    status: "recording", // so the "Stop & summarize" button renders
    error: null,
    liveTranscript: [],
    interim: { me: "", them: "" },
    begin: jest.fn(),
    stop,
    failCapture,
  });
  mockUseMeeting.mockReturnValue({
    meeting: { title: "Sync", user_notes: "" },
    saveNotes: jest.fn().mockResolvedValue(undefined),
  });
  mockUseMeetings.mockReturnValue({ endMeeting });

  render(<LiveMeetingPage params={{ id: "m-1" }} />);
}

async function clickStop() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /stop & summarize/i }));
  });
}

beforeEach(() => {
  push.mockReset();
  // jsdom doesn't implement scrollIntoView (LiveTranscript auto-scrolls).
  window.HTMLElement.prototype.scrollIntoView = jest.fn();
});

describe("LiveMeetingPage finalize() failure discrimination (findings #3/#5)", () => {
  it("routes a 429 (over budget) through failCapture and does NOT navigate away", async () => {
    setup(() => Promise.reject(new ApiError(429, "Monthly AI usage limit reached")));
    await clickStop();

    expect(endMeeting).toHaveBeenCalledWith("m-1");
    expect(failCapture).toHaveBeenCalledTimes(1);
    expect(failCapture.mock.calls[0][0]).toMatch(/monthly ai limit/i);
    expect(push).not.toHaveBeenCalled(); // no dead-end navigation
  });

  it("routes a 500/network failure through failCapture and does NOT navigate away", async () => {
    setup(() => Promise.reject(new Error("network down")));
    await clickStop();

    expect(failCapture).toHaveBeenCalledTimes(1);
    expect(failCapture.mock.calls[0][0]).toMatch(/try again/i);
    expect(push).not.toHaveBeenCalled();
  });

  it("treats a 404 as a benign already-ended race: navigates to detail, no error", async () => {
    setup(() => Promise.reject(new ApiError(404, "meeting not found or not recording")));
    await clickStop();

    expect(failCapture).not.toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith("/meetings/m-1");
  });

  it("navigates to detail on success", async () => {
    setup(() => Promise.resolve({ meeting_id: "m-1", status: "processing" }));
    await clickStop();

    expect(failCapture).not.toHaveBeenCalled();
    expect(push).toHaveBeenCalledWith("/meetings/m-1");
  });
});
