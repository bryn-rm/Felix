/**
 * Tests for EmailCard component.
 */
import "@testing-library/jest-dom";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EmailCard } from "@/components/inbox/EmailCard";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockPush = jest.fn();
jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, replace: jest.fn(), back: jest.fn() }),
}));

const mockApiPost = jest.fn();
jest.mock("@/lib/api", () => ({
  api: {
    post: (...args: unknown[]) => mockApiPost(...args),
  },
}));

// ---------------------------------------------------------------------------
// Fixture factory
// ---------------------------------------------------------------------------

function makeEmail(overrides: Record<string, unknown> = {}) {
  return {
    id: "email-1",
    thread_id: "thread-1",
    user_id: "user-1",
    gmail_id: "gmail-1",
    from_email: "alice@example.com",
    from_name: "Alice Sender",
    to_email: "me@example.com",
    subject: "Test Subject",
    snippet: "Email snippet text",
    received_at: new Date(Date.now() - 30 * 60_000).toISOString(),
    category: "action_required",
    urgency: "medium",
    sentiment: null,
    topic: null,
    draft_generated: false,
    ...overrides,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any;
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  mockPush.mockReset();
  mockApiPost.mockReset();
  mockApiPost.mockResolvedValue({});
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("EmailCard", () => {
  it("renders sender name and subject", () => {
    render(<EmailCard email={makeEmail()} />);
    expect(screen.getByText("Alice Sender")).toBeInTheDocument();
    expect(screen.getByText("Test Subject")).toBeInTheDocument();
  });

  it("renders correct urgency badge colour for critical", () => {
    render(<EmailCard email={makeEmail({ urgency: "critical" })} />);
    const badge = screen.getByText("critical");
    expect(badge).toHaveClass("bg-red-500/20");
    expect(badge).toHaveClass("text-red-400");
  });

  it("renders correct urgency badge colour for low", () => {
    render(<EmailCard email={makeEmail({ urgency: "low" })} />);
    const badge = screen.getByText("low");
    expect(badge).toHaveClass("bg-slate-500/20");
    expect(badge).toHaveClass("text-slate-400");
  });

  it("thumbs down click opens category popover", () => {
    render(<EmailCard email={makeEmail()} />);

    expect(screen.queryByText("Correct category")).not.toBeInTheDocument();

    // The button has opacity-0 when not hovered but is still in the DOM
    fireEvent.click(screen.getByRole("button", { name: "Correct category" }));

    expect(screen.getByText("Correct category")).toBeInTheDocument();
  });

  it("category selection fires feedback POST", async () => {
    render(<EmailCard email={makeEmail({ id: "email-42" })} />);

    // Open the popover
    fireEvent.click(screen.getByRole("button", { name: "Correct category" }));

    // Click a category option inside the popover
    const newsletterBtn = screen.getByRole("button", { name: /newsletter/i });
    fireEvent.click(newsletterBtn);

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        "/eval/feedback",
        expect.objectContaining({
          ai_call_id: "email-42",
          feature: "triage",
          rating: 1,
          correction: "newsletter",
        }),
      );
    });
  });
});
