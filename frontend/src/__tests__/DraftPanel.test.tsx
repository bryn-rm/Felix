/**
 * Tests for DraftPanel component.
 *
 * useDraft is mocked so each test can inject a specific state without
 * needing a real API server or streaming setup.
 */
import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DraftPanel } from "@/components/email/DraftPanel";
import type { Draft } from "@/lib/types";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn() }),
}));

// We control what useDraft returns per-test via draftHookValue
type DraftHookValue = {
  draft: Partial<Draft> | null;
  draftText: string;
  state: "loading" | "generating" | "ready" | "sending" | "sent" | "error";
  error: string | null;
  send: jest.Mock<Promise<void>, [string]>;
  discard: jest.Mock<Promise<void>, []>;
};

let draftHookValue: DraftHookValue;
jest.mock("@/hooks/useDraft", () => ({
  useDraft: () => draftHookValue,
}));

// ---------------------------------------------------------------------------
// Fixture factory
// ---------------------------------------------------------------------------

function makeDraftHook(overrides: Partial<DraftHookValue> = {}): DraftHookValue {
  return {
    draft: null,
    draftText: "",
    state: "loading",
    error: null,
    send: jest.fn().mockResolvedValue(undefined),
    discard: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockPush.mockReset();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DraftPanel", () => {
  it("shows generating state on mount when no draft exists", () => {
    draftHookValue = makeDraftHook({
      state: "generating",
      draftText: "I hope this find",
    });
    render(<DraftPanel emailId="email-1" />);
    expect(screen.getByText("Generating draft…")).toBeInTheDocument();
  });

  it("shows draft text when draft already exists", () => {
    draftHookValue = makeDraftHook({
      draft: { id: "draft-1", draft_text: "Hello, thanks for reaching out.", status: "pending" },
      draftText: "Hello, thanks for reaching out.",
      state: "ready",
    });
    render(<DraftPanel emailId="email-1" />);
    expect(screen.getByRole("textbox")).toHaveValue("Hello, thanks for reaching out.");
  });

  it("send button calls send endpoint with edited text", async () => {
    const mockSend = jest.fn().mockResolvedValue(undefined);
    draftHookValue = makeDraftHook({
      state: "ready",
      draftText: "Original generated text",
      send: mockSend,
    });
    render(<DraftPanel emailId="email-1" />);

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "Edited reply text" } });

    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => {
      expect(mockSend).toHaveBeenCalledWith("Edited reply text");
    });
  });

  it("fires eval feedback when text was edited before send", async () => {
    // DraftPanel passes the user's edited text to useDraft.send().
    // useDraft.send() then detects the edit and fires the eval POST.
    // This test confirms DraftPanel correctly forwards the *modified* text.
    const mockSend = jest.fn().mockResolvedValue(undefined);
    const originalText = "Original generated text";
    const editedText = "This has been changed by the user";

    draftHookValue = makeDraftHook({
      draft: { id: "draft-1", draft_text: originalText },
      state: "ready",
      draftText: originalText,
      send: mockSend,
    });
    render(<DraftPanel emailId="email-1" />);

    // User edits the text
    fireEvent.change(screen.getByRole("textbox"), { target: { value: editedText } });

    // User clicks Send
    fireEvent.click(screen.getByRole("button", { name: /^send$/i }));

    await waitFor(() => {
      expect(mockSend).toHaveBeenCalledWith(editedText);
    });
    // The text passed to send differs from original — useDraft will fire eval feedback
    expect(mockSend.mock.calls[0][0]).not.toBe(originalText);
  });

  it("discard shows confirmation dialog", () => {
    draftHookValue = makeDraftHook({
      draft: { id: "draft-1", draft_text: "Some generated text." },
      state: "ready",
      draftText: "Some generated text.",
    });
    render(<DraftPanel emailId="email-1" />);

    // Click the Discard button in the footer
    const discardBtns = screen.getAllByRole("button", { name: /discard/i });
    fireEvent.click(discardBtns[0]);

    // Confirmation dialog should appear
    expect(screen.getByText("Discard this draft?")).toBeInTheDocument();
  });
});
