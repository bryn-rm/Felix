import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SWRConfig } from "swr";

import LiveAssistViewerPage from "@/app/(app)/meetings/live/[id]/viewer/page";

// Real SWR, mocked transport: this test is about what the viewer does to the
// outside world, so the network boundary is the thing worth observing.
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api");
  return {
    ...actual,
    api: { get: jest.fn(), post: jest.fn(), put: jest.fn(), del: jest.fn() },
  };
});
import { ApiError, api } from "@/lib/api";

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockPut = api.put as jest.Mock;
const mockDel = api.del as jest.Mock;

const card = {
  id: "i-1",
  kind: "fact",
  source: "proactive",
  question: null,
  title: "Renewal is Friday",
  body: "Agreed by email last week.",
  transcript_ts: 12.5,
  dismissed: false,
  request_id: null,
  expansion_options: [],
  created_at: "2026-08-23T10:00:00Z",
};

const liveMeeting = {
  id: "m-1",
  title: "Roadmap",
  status: "recording",
  source: "browser_capture",
  template: "general",
  meeting_type: "general",
  user_role: null,
  started_at: "2026-08-23T09:55:00Z",
  ended_at: null,
};

function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    meeting: liveMeeting,
    items: [card],
    capture_attached: true,
    ...overrides,
  };
}

let openedSockets: string[];
let getUserMedia: jest.Mock;
let getDisplayMedia: jest.Mock;
let realWebSocket: typeof WebSocket;
let scrollIntoViewDescriptor: PropertyDescriptor | undefined;

function renderViewer() {
  return render(
    // Fresh cache per test so one test's snapshot can't satisfy the next.
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <LiveAssistViewerPage params={{ id: "m-1" }} />
    </SWRConfig>,
  );
}

/** The one element on the page that scrolls: the card stream. */
function cardStream(): HTMLElement {
  const list = screen
    .getByText("Renewal is Friday")
    .closest("div[class*='overflow-y-auto']");
  if (!list) throw new Error("card stream not found");
  return list as HTMLElement;
}

async function typeAndSend(question: string) {
  fireEvent.change(screen.getByLabelText(/ask felix a question/i), {
    target: { value: question },
  });
  // The send settles asynchronously; flushing inside act keeps the resulting
  // state updates out of React's "not wrapped in act" warning.
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /send question/i }));
  });
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  mockDel.mockReset();

  openedSockets = [];
  realWebSocket = global.WebSocket;
  scrollIntoViewDescriptor = Object.getOwnPropertyDescriptor(
    window.HTMLElement.prototype,
    "scrollIntoView",
  );
  // Any attempt to open the capture socket is recorded rather than performed.
  global.WebSocket = class {
    constructor(url: string) {
      openedSockets.push(url);
    }
  } as unknown as typeof WebSocket;

  getUserMedia = jest.fn();
  getDisplayMedia = jest.fn();
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia, getDisplayMedia },
    configurable: true,
  });
});

afterEach(() => {
  global.WebSocket = realWebSocket;
  if (scrollIntoViewDescriptor) {
    Object.defineProperty(
      window.HTMLElement.prototype,
      "scrollIntoView",
      scrollIntoViewDescriptor,
    );
  } else {
    Reflect.deleteProperty(window.HTMLElement.prototype, "scrollIntoView");
  }
});

describe("LiveAssistViewerPage — capture isolation", () => {
  it("never opens a capture socket or asks for microphone / screen access", async () => {
    mockGet.mockResolvedValue(snapshot());
    renderViewer();
    await screen.findByText("Renewal is Friday");

    // The invariant that survives the phone becoming interactive: it can ask
    // questions, but it can never initialise recording or STT, and it cannot
    // take capture ownership from the device that started the meeting.
    expect(openedSockets).toEqual([]);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(getDisplayMedia).not.toHaveBeenCalled();
  });

  it("asking goes over REST and still opens no socket", async () => {
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockResolvedValue({ item: { ...card, id: "i-2", title: "Answer" } });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");

    await waitFor(() => expect(mockPost).toHaveBeenCalled());
    expect(mockPost.mock.calls[0][0]).toBe("/meetings/m-1/assist/ask");
    expect(openedSockets).toEqual([]);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(getDisplayMedia).not.toHaveBeenCalled();
  });

  it("loads with one read and writes nothing until the user acts", async () => {
    mockGet.mockResolvedValue(snapshot());
    renderViewer();
    await screen.findByText("Renewal is Friday");

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet).toHaveBeenCalledWith("/meetings/m-1/live-view");
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPut).not.toHaveBeenCalled();
    expect(mockDel).not.toHaveBeenCalled();
  });

  it("exposes no recording or notes controls", async () => {
    mockGet.mockResolvedValue(snapshot());
    renderViewer();
    await screen.findByText("Renewal is Friday");

    expect(
      screen.queryByRole("button", { name: /start recording/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /stop & summarize/i }),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/notes/i)).not.toBeInTheDocument();
  });
});

describe("LiveAssistViewerPage — asking", () => {
  it("sends the question and shows the answer without waiting for the next poll", async () => {
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockResolvedValue({
      item: { ...card, id: "i-2", source: "ask", title: "They agreed £40" },
    });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");

    expect(await screen.findByText("They agreed £40")).toBeInTheDocument();
    expect(mockPost.mock.calls[0][1]).toMatchObject({
      question: "What did they agree?",
      intent: "answer",
    });
    // A request_id makes the ask idempotent server-side, so a retry replays the
    // first answer instead of paying for a second.
    expect(mockPost.mock.calls[0][1].request_id).toBeTruthy();
  });

  it("keeps the typed question when the ask fails, and clears pending", async () => {
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockRejectedValue(new Error("network down"));
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");

    const box = await screen.findByLabelText(/ask felix a question/i);
    // Nothing typed mid-meeting should ever be lost to a failed send.
    await waitFor(() => expect(box).toHaveValue("What did they agree?"));
    // Pending must clear or the box stays disabled until a full reload.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /send question/i })).toBeEnabled(),
    );
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("offers no ask box once the meeting has ended", async () => {
    // The server refuses an ask on a closed session, so offering one here would
    // only produce a rejection.
    mockGet.mockResolvedValue(
      snapshot({ meeting: { ...liveMeeting, status: "done" } }),
    );
    renderViewer();
    await screen.findByText(/meeting ended/i);

    expect(
      screen.queryByLabelText(/ask felix a question/i),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/no new cards will appear/i)).toBeInTheDocument();
  });

  it("dismisses a card against the shared persisted state", async () => {
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockResolvedValue({ dismissed: true });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /dismiss suggestion/i }));
    });

    await waitFor(() =>
      expect(mockPost).toHaveBeenCalledWith(
        "/meetings/m-1/assist/i-1/dismiss",
        {},
      ),
    );
    // Optimistic hide, then the server row is what both devices read back.
    await waitFor(() =>
      expect(screen.queryByText("Renewal is Friday")).not.toBeInTheDocument(),
    );
  });
});

describe("LiveAssistViewerPage — status", () => {
  it("shows meeting identity, live status and the persisted cards", async () => {
    mockGet.mockResolvedValue(
      snapshot({
        meeting: { ...liveMeeting, meeting_type: "interview", user_role: "candidate" },
      }),
    );
    renderViewer();

    expect(await screen.findByText("Roadmap")).toBeInTheDocument();
    expect(screen.getByText(/recording on another device/i)).toBeInTheDocument();
    expect(screen.getByText("Interview · Candidate")).toBeInTheDocument();
    expect(screen.getByText("Renewal is Friday")).toBeInTheDocument();
  });

  it("reports a dropped capture socket from server state, not from its own UI", async () => {
    // meetings.status is still 'recording' here — only capture_attached says
    // the capturing device is gone.
    mockGet.mockResolvedValue(snapshot({ capture_attached: false }));
    renderViewer();

    expect(
      await screen.findByText(/recording device isn’t connected/i),
    ).toBeInTheDocument();
    // Asks don't use that socket, so they stay available and say so.
    expect(screen.getByText(/still ask questions/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/ask felix a question/i)).toBeInTheDocument();
  });

  it("reports a live manual session without claiming it is recording", async () => {
    mockGet.mockResolvedValue(
      snapshot({
        meeting: { ...liveMeeting, source: "manual_notes" },
        capture_attached: null,
      }),
    );
    renderViewer();

    await screen.findByText("Renewal is Friday");
    expect(
      screen.queryByText(/recording device isn’t connected/i),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/manual notes · no recording/i)).toBeInTheDocument();
    expect(screen.queryByText(/recording on another device/i)).not.toBeInTheDocument();
  });

  it("becomes an ended, read-only view once the meeting is over", async () => {
    mockGet.mockResolvedValue(
      snapshot({
        meeting: { ...liveMeeting, status: "done", ended_at: "2026-08-23T10:30:00Z" },
      }),
    );
    renderViewer();

    expect(await screen.findByText(/meeting ended/i)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view the summary/i })).toHaveAttribute(
      "href",
      "/meetings/m-1",
    );
    // Cards written during the meeting stay readable afterwards.
    expect(screen.getByText("Renewal is Friday")).toBeInTheDocument();
  });

  it("fails closed when live assist is off (the endpoint 404s)", async () => {
    mockGet.mockRejectedValue(new Error("Not found"));
    renderViewer();

    expect(
      await screen.findByText(/live assist isn’t available for this meeting/i),
    ).toBeInTheDocument();
    expect(openedSockets).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Phase 3: the phone as something someone actually holds through a meeting
// ---------------------------------------------------------------------------

describe("LiveAssistViewerPage — mobile use", () => {
  it("shows the question is being worked on where the answer will appear", async () => {
    // The composer's own pending line is the first thing the mobile keyboard
    // covers, so the stream has to say it too.
    mockGet.mockResolvedValue(snapshot());
    let settle: (value: unknown) => void = () => {};
    mockPost.mockReturnValue(new Promise((resolve) => { settle = resolve; }));
    const { container } = renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");

    expect(await screen.findByText(/felix is working on your question/i)).toBeInTheDocument();
    expect(screen.queryByText(/asking felix/i)).not.toBeInTheDocument();
    expect(container.querySelectorAll(".animate-spin")).toHaveLength(1);

    await act(async () => {
      settle({ item: { ...card, id: "i-2", source: "ask", title: "They agreed £40" } });
    });
    await waitFor(() =>
      expect(screen.queryByText(/felix is working on your question/i)).not.toBeInTheDocument(),
    );
    expect(screen.getByText("They agreed £40")).toBeInTheDocument();
  });

  it("reveals an explicit ask and keeps following after its own scroll event", async () => {
    mockGet.mockResolvedValue(snapshot());
    let settle: (value: unknown) => void = () => {};
    mockPost.mockReturnValue(new Promise((resolve) => { settle = resolve; }));

    const offsetTopDescriptor = Object.getOwnPropertyDescriptor(
      window.HTMLElement.prototype,
      "offsetTop",
    );
    Object.defineProperty(window.HTMLElement.prototype, "offsetTop", {
      configurable: true,
      get() {
        const element = this as HTMLElement;
        const text = element.textContent ?? "";
        if (element.className.includes("border-indigo-500/20")) return 900;
        if (
          element.parentElement?.className.includes("overflow-y-auto") &&
          text.includes("They agreed £40")
        ) {
          return 1200;
        }
        return 0;
      },
    });

    try {
      renderViewer();
      await screen.findByText("Renewal is Friday");
      const list = cardStream();
      Object.defineProperty(list, "scrollHeight", { value: 2400, configurable: true });
      Object.defineProperty(list, "clientHeight", { value: 400, configurable: true });

      // The reader was looking at an older card before asking explicitly.
      list.scrollTop = 300;
      fireEvent.scroll(list);

      await typeAndSend("What did they agree?");
      expect(await screen.findByText(/felix is working on your question/i)).toBeInTheDocument();
      expect(list.scrollTop).toBe(900);

      // Browsers dispatch this for the assignment above. It must not be read as
      // the user abandoning follow mode just because the target is tall.
      fireEvent.scroll(list);

      await act(async () => {
        settle({ item: { ...card, id: "i-2", source: "ask", title: "They agreed £40" } });
      });
      await screen.findByText("They agreed £40");
      expect(list.scrollTop).toBe(1200);
    } finally {
      if (offsetTopDescriptor) {
        Object.defineProperty(
          window.HTMLElement.prototype,
          "offsetTop",
          offsetTopDescriptor,
        );
      } else {
        Reflect.deleteProperty(window.HTMLElement.prototype, "offsetTop");
      }
    }
  });

  it("does not drag the page when a card arrives — only the card stream moves", async () => {
    // scrollIntoView on a phone scrolls the app shell around the viewer, not
    // just the stream. The stream scrolls itself instead.
    const scrollIntoView = jest.fn();
    window.HTMLElement.prototype.scrollIntoView = scrollIntoView;
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockResolvedValue({
      item: { ...card, id: "i-2", source: "ask", title: "They agreed £40" },
    });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");
    await screen.findByText("They agreed £40");

    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("leaves the scroll position alone while an older card is being read", async () => {
    // Cards arrive unasked-for mid-meeting. Following the newest one is right
    // at the live edge and wrong halfway up the stream.
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockResolvedValue({
      item: { ...card, id: "i-2", source: "ask", title: "They agreed £40" },
    });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    const list = cardStream();
    Object.defineProperty(list, "scrollHeight", { value: 2000, configurable: true });
    Object.defineProperty(list, "clientHeight", { value: 400, configurable: true });
    list.scrollTop = 500; // 1100px from the bottom — reading, not following
    fireEvent.scroll(list);

    await typeAndSend("What did they agree?");
    await screen.findByText("They agreed £40");

    expect(list.scrollTop).toBe(500);
  });

  it("keeps the meeting's identity and state pinned outside the scrolling stream", async () => {
    mockGet.mockResolvedValue(
      snapshot({
        meeting: { ...liveMeeting, meeting_type: "interview", user_role: "candidate" },
      }),
    );
    renderViewer();

    const title = await screen.findByRole("heading", { name: "Roadmap" });
    expect(cardStream().contains(title)).toBe(false);
    expect(screen.getByText("Interview · Candidate")).toBeInTheDocument();
    expect(screen.getByText(/recording on another device/i)).toBeInTheDocument();
  });

  it("always says which device owns recording and notes once cards arrive", async () => {
    mockGet.mockResolvedValue(snapshot());
    renderViewer();
    await screen.findByText("Renewal is Friday");

    expect(screen.getByText(/second screen/i)).toBeInTheDocument();
    expect(screen.getByText(/recording and notes stay on the device/i)).toBeInTheDocument();
    // And no capture controls have crept in with the polish.
    expect(screen.queryByRole("button", { name: /start recording/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop & summarize/i })).not.toBeInTheDocument();
    expect(openedSockets).toEqual([]);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(getDisplayMedia).not.toHaveBeenCalled();
  });
});

describe("LiveAssistViewerPage — the other device is already asking", () => {
  it("surfaces the server's reason rather than a generic failure", async () => {
    // One typed answer runs per meeting at a time. When the laptop holds that
    // slot the phone has no other way to know why nothing happened.
    mockGet.mockResolvedValue(snapshot());
    mockPost.mockRejectedValue(
      new ApiError(
        400,
        "Felix is already answering another question for this meeting — try again once it finishes.",
      ),
    );
    renderViewer();
    await screen.findByText("Renewal is Friday");

    await typeAndSend("What did they agree?");

    expect(
      await screen.findByText(/already answering another question/i),
    ).toBeInTheDocument();
    // And the question survives, so retrying is one tap.
    await waitFor(() =>
      expect(screen.getByLabelText(/ask felix a question/i)).toHaveValue(
        "What did they agree?",
      ),
    );
  });
});
