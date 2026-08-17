import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";

import LiveMeetingPage from "@/app/(app)/meetings/live/[id]/page";
import { ApiError } from "@/lib/api";
import { useMeetingCapture } from "@/hooks/useMeetingCapture";
import { useMeeting, useMeetings } from "@/hooks/useMeetings";
import { useManualAssist } from "@/hooks/useManualAssist";

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
jest.mock("@/hooks/useManualAssist", () => ({
  useManualAssist: jest.fn(),
}));
// The page reads the live_assist_mode flag via useSWR("/settings"); mock swr so
// tests control the flag without touching the network. useAssistItems shares
// the same mock (its fetch stays empty).
jest.mock("swr", () => ({ __esModule: true, default: jest.fn() }));
import useSWR from "swr";

const mockUseMeetingCapture = useMeetingCapture as jest.Mock;
const mockUseMeeting = useMeeting as jest.Mock;
const mockUseMeetings = useMeetings as jest.Mock;
const mockUseManualAssist = useManualAssist as jest.Mock;
const mockUseSWR = useSWR as unknown as jest.Mock;

let failCapture: jest.Mock;
let stop: jest.Mock;
let endMeeting: jest.Mock;
let sendAsk: jest.Mock;
let manualSendAsk: jest.Mock;

interface SetupOptions {
  assistFlag?: boolean;
  assistItems?: unknown[];
  meeting?: Record<string, unknown>;
}

function setup(
  endMeetingImpl: () => Promise<unknown>,
  { assistFlag = false, assistItems = [], meeting = {} }: SetupOptions = {},
) {
  failCapture = jest.fn();
  stop = jest.fn().mockResolvedValue(undefined);
  endMeeting = jest.fn().mockImplementation(endMeetingImpl);
  sendAsk = jest.fn(() => true);
  manualSendAsk = jest.fn(() => true);
  mockUseManualAssist.mockReturnValue({
    items: [],
    sendAsk: manualSendAsk,
    askPending: false,
    askError: null,
  });

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
    meeting: { title: "Sync", user_notes: "", ...meeting },
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
  it("runs the notes and assistant workspace without starting capture", async () => {
    setup(() => Promise.resolve({ meeting_id: "m-1", status: "processing" }), {
      assistFlag: true,
      meeting: { source: "manual_notes", status: "recording" },
    });

    expect(screen.getByText(/manual notes · no recording/i)).toBeInTheDocument();
    expect(screen.queryByText("Live transcript")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start recording/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/ask about previous meetings/i).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText(/ask felix a question/i), {
      target: { value: "What did we decide last time?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });
    expect(manualSendAsk).toHaveBeenCalledWith("What did we decide last time?");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /finish & summarize/i }));
    });
    expect(stop).not.toHaveBeenCalled();
    expect(endMeeting).toHaveBeenCalledWith("m-1");
  });

  it("shows the explicitly selected interview mode", () => {
    setup(() => Promise.resolve({}), {
      meeting: {
        meeting_type: "interview",
        user_role: "candidate",
        template: "interview",
      },
    });

    expect(screen.getByText("Interview · Candidate")).toBeInTheDocument();
  });

  it("renders no assist affordance when live_assist_mode is off", () => {
    setup(() => Promise.resolve({}), { assistFlag: false });

    expect(
      screen.queryByRole("button", { name: /toggle live assist/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/live assist/i)).not.toBeInTheDocument();
  });

  it("starts collapsed with an unseen badge, opens on toggle, and asks via the socket", async () => {
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
    await act(async () => {
      fireEvent.click(screen.getAllByLabelText(/send question/i)[0]);
    });
    expect(sendAsk).toHaveBeenCalledWith("Who is Sarah?");
  });
});

// ---------------------------------------------------------------------------
// Loading: `source` decides which UI this page is, so neither may render first
// ---------------------------------------------------------------------------

describe("LiveMeetingPage before the meeting row loads", () => {
  it("offers neither capture nor manual affordances while the row is undefined", () => {
    setup(() => Promise.resolve({}), { assistFlag: true });
    mockUseMeeting.mockReturnValue({
      meeting: undefined,
      isLoading: true,
      saveNotes: jest.fn(),
    });
    mockUseMeetingCapture.mockReturnValue({
      ...mockUseMeetingCapture.mock.results[0].value,
      status: "idle",
    });
    render(<LiveMeetingPage params={{ id: "m-1" }} />);

    // The bug this guards: a manual session rendered the capture branch during
    // this window, so "Start recording" would prompt for the mic and a tab
    // share on a flow whose whole premise is that it never records.
    const started = screen.queryAllByRole("button", { name: /start recording/i });
    expect(started).toHaveLength(0);
    expect(screen.queryByText(/ready to capture/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(/loading/i).length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// A finished manual session must not accept more notes
// ---------------------------------------------------------------------------

describe("LiveMeetingPage manual session after it is finished", () => {
  it("shows the notes read-only once the session is no longer recording", () => {
    setup(() => Promise.resolve({}), {
      assistFlag: true,
      meeting: {
        source: "manual_notes",
        status: "done",
        user_notes: "Agreed to ship on Friday.",
      },
    });

    // The summary was built from these notes; an editable box here would let
    // meetings.user_notes drift away from the summary rendered for it.
    expect(screen.queryByPlaceholderText(/jot down what matters/i)).not.toBeInTheDocument();
    expect(screen.getByText("Agreed to ship on Friday.")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /finish & summarize/i }),
    ).not.toBeInTheDocument();
  });
});
