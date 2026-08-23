import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
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
import { api } from "@/lib/api";

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
  expansion_options: ["code"],
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

let openedSockets: string[];
let getUserMedia: jest.Mock;
let getDisplayMedia: jest.Mock;
let realWebSocket: typeof WebSocket;

function renderViewer() {
  return render(
    // Fresh cache per test so one test's snapshot can't satisfy the next.
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <LiveAssistViewerPage params={{ id: "m-1" }} />
    </SWRConfig>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockPut.mockReset();
  mockDel.mockReset();

  openedSockets = [];
  realWebSocket = global.WebSocket;
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
});

describe("LiveAssistViewerPage — capture isolation", () => {
  it("never opens a capture socket or asks for microphone / screen access", async () => {
    mockGet.mockResolvedValue({ meeting: liveMeeting, items: [card] });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    // The whole point of the phone surface: it is a reader. Nothing it does
    // can initialise recording or STT, or take capture ownership from the
    // device that started the meeting.
    expect(openedSockets).toEqual([]);
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(getDisplayMedia).not.toHaveBeenCalled();
  });

  it("issues one read and no writes", async () => {
    mockGet.mockResolvedValue({ meeting: liveMeeting, items: [card] });
    renderViewer();
    await screen.findByText("Renewal is Friday");

    expect(mockGet).toHaveBeenCalledTimes(1);
    expect(mockGet).toHaveBeenCalledWith("/meetings/m-1/live-view");
    // No dismiss, no ask, no lifecycle — the viewer cannot mutate the session.
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockPut).not.toHaveBeenCalled();
    expect(mockDel).not.toHaveBeenCalled();
  });

  it("exposes no recording, notes or ask controls", async () => {
    mockGet.mockResolvedValue({ meeting: liveMeeting, items: [card] });
    const { container } = renderViewer();
    await screen.findByText("Renewal is Friday");

    expect(
      screen.queryByRole("button", { name: /start recording/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /stop & summarize/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /dismiss suggestion/i }),
    ).not.toBeInTheDocument();
    // The ask box (a textarea) and the notes editor both write to the session.
    expect(container.querySelector("textarea")).toBeNull();
    // Expansion buttons cost an AI call, so they belong to the capture client.
    expect(
      screen.queryByRole("button", { name: /full solution/i }),
    ).not.toBeInTheDocument();
  });
});

describe("LiveAssistViewerPage — rendering", () => {
  it("shows meeting identity, live status and the persisted cards", async () => {
    mockGet.mockResolvedValue({
      meeting: { ...liveMeeting, meeting_type: "interview", user_role: "candidate" },
      items: [card],
    });
    renderViewer();

    expect(await screen.findByText("Roadmap")).toBeInTheDocument();
    expect(screen.getByText(/recording on another device/i)).toBeInTheDocument();
    expect(screen.getByText("Interview · Candidate")).toBeInTheDocument();
    expect(screen.getByText("Renewal is Friday")).toBeInTheDocument();
  });

  it("becomes an ended, read-only view once the meeting is over", async () => {
    mockGet.mockResolvedValue({
      meeting: { ...liveMeeting, status: "done", ended_at: "2026-08-23T10:30:00Z" },
      items: [card],
    });
    renderViewer();

    expect(await screen.findByText(/meeting ended/i)).toBeInTheDocument();
    expect(screen.getByText(/no new cards will appear/i)).toBeInTheDocument();
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
