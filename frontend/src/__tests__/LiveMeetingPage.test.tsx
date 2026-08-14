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
// The page reads the live_assist_mode flag via useSWR("/settings"); mock swr so
// tests control the flag without touching the network. useAssistItems shares
// the same mock (its fetch stays empty).
jest.mock("swr", () => ({ __esModule: true, default: jest.fn() }));
import useSWR from "swr";

const mockUseMeetingCapture = useMeetingCapture as jest.Mock;
const mockUseMeeting = useMeeting as jest.Mock;
const mockUseMeetings = useMeetings as jest.Mock;
const mockUseSWR = useSWR as unknown as jest.Mock;

let failCapture: jest.Mock;
let stop: jest.Mock;
let endMeeting: jest.Mock;
let sendAsk: jest.Mock;

interface SetupOptions {
  assistFlag?: boolean;
  assistItems?: unknown[];
}

function setup(
  endMeetingImpl: () => Promise<unknown>,
  { assistFlag = false, assistItems = [] }: SetupOptions = {},
) {
  failCapture = jest.fn();
  stop = jest.fn().mockResolvedValue(undefined);
  endMeeting = jest.fn().mockImplementation(endMeetingImpl);
  sendAsk = jest.fn(() => true);

  mockUseSWR.mockImplementation((key: string | null) =>
    key === "/settings"
      ? { data: { live_assist_mode: assistFlag }, mutate: jest.fn() }
      : { data: undefined, mutate: jest.fn() },
  );
  mockUseMeetingCapture.mockReturnValue({
    status: "recording", // so the "Stop & summarize" button renders
    error: null,
    liveTranscript: [],
    interim: { me: "", them: "" },
    assistItems,
    sendAsk,
    askPending: false,
    askError: null,
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

// ---------------------------------------------------------------------------
// Live assist sidebar — fail-closed gating + collapsed-by-default UX
// ---------------------------------------------------------------------------

const sampleCard = {
  id: "i-1",
  kind: "fact",
  source: "proactive",
  question: null,
  title: "Renewal is Friday",
  body: "Agreed by email last week.",
  transcript_ts: 12,
  dismissed: false,
  created_at: "2026-08-10T10:00:00Z",
};

describe("LiveMeetingPage live assist", () => {
  it("renders no assist affordance when live_assist_mode is off", () => {
    setup(() => Promise.resolve({}), { assistFlag: false });

    expect(
      screen.queryByRole("button", { name: /toggle live assist/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/live assist/i)).not.toBeInTheDocument();
  });

  it("starts collapsed with an unseen badge, opens on toggle, and asks via the socket", () => {
    setup(() => Promise.resolve({}), {
      assistFlag: true,
      assistItems: [sampleCard],
    });

    // Collapsed by default — the card is not visible, the badge counts it.
    const toggle = screen.getByRole("button", { name: /toggle live assist/i });
    expect(screen.queryByText("Renewal is Friday")).not.toBeInTheDocument();
    expect(toggle).toHaveTextContent("1");

    fireEvent.click(toggle);
    // Open: card visible (desktop column + mobile drawer render one each).
    expect(screen.getAllByText("Renewal is Friday").length).toBeGreaterThan(0);

    // Ask box routes through the capture hook's sendAsk.
    const inputs = screen.getAllByLabelText(/ask felix a question/i);
    fireEvent.change(inputs[0], { target: { value: "Who is Sarah?" } });
    fireEvent.click(screen.getAllByLabelText(/send question/i)[0]);
    expect(sendAsk).toHaveBeenCalledWith("Who is Sarah?");
  });
});
