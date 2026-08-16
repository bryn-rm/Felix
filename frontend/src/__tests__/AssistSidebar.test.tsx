import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";

import { AssistSidebar } from "@/components/meetings/AssistSidebar";
import type { AssistItem } from "@/lib/types";

const items: AssistItem[] = [
  {
    id: "i-1",
    kind: "fact",
    source: "proactive",
    question: null,
    title: "Renewal is Friday",
    body: "Agreed by email last week.",
    transcript_ts: 754,
    dismissed: false,
    request_id: null,
    created_at: "2026-08-10T10:00:00Z",
  },
  {
    id: "i-2",
    kind: "answer",
    source: "ask",
    question: "What deadline did Sarah mention?",
    title: "Sarah's deadline",
    body: "She said end of the month.",
    transcript_ts: null,
    dismissed: false,
    request_id: "req-1",
    created_at: "2026-08-10T10:05:00Z",
  },
];

function setup(overrides: Partial<Parameters<typeof AssistSidebar>[0]> = {}) {
  const props = {
    items,
    onDismiss: jest.fn(),
    onAsk: jest.fn(() => true),
    askPending: false,
    askError: null,
    onClose: jest.fn(),
    ...overrides,
  };
  render(<AssistSidebar {...props} />);
  return props;
}

describe("AssistSidebar", () => {
  it("renders cards with kind badges, timestamps, and ask questions", () => {
    setup();

    expect(screen.getByText("Renewal is Friday")).toBeInTheDocument();
    expect(screen.getByText("Fact")).toBeInTheDocument();
    expect(screen.getByText("12:34")).toBeInTheDocument(); // 754s
    expect(screen.getByText("Answer")).toBeInTheDocument();
    expect(
      screen.getByText(/what deadline did sarah mention/i),
    ).toBeInTheDocument();
  });

  it("shows the quiet empty state when there are no cards", () => {
    setup({ items: [] });
    expect(screen.getByText(/listening quietly/i)).toBeInTheDocument();
  });

  it("dismiss routes the card id up", () => {
    const props = setup();
    fireEvent.click(screen.getAllByLabelText(/dismiss suggestion/i)[0]);
    expect(props.onDismiss).toHaveBeenCalledWith("i-1");
  });

  it("submits a trimmed question and clears the input", () => {
    const props = setup();
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "  Who is Sarah?  " } });
    fireEvent.click(screen.getByLabelText(/send question/i));

    expect(props.onAsk).toHaveBeenCalledWith("  Who is Sarah?  ");
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("sends on Enter and keeps Shift+Enter as a newline", () => {
    const props = setup();
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Who is Sarah?" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(props.onAsk).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onAsk).toHaveBeenCalledWith("Who is Sarah?");
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("disables submit while an ask is pending and shows errors", () => {
    setup({ askPending: true, askError: "over budget" });
    expect(screen.getByLabelText(/send question/i)).toBeDisabled();
    expect(screen.getByText("over budget")).toBeInTheDocument();
  });

  it("keeps the typed question when the send fails (e.g. reconnecting)", () => {
    const props = setup({ onAsk: jest.fn(() => false) });
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Who is Sarah?" } });
    fireEvent.click(screen.getByLabelText(/send question/i));

    expect(props.onAsk).toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("Who is Sarah?");
  });

  it("renders interview markdown and requests a linked expansion", () => {
    const interviewItem: AssistItem = {
      ...items[1],
      id: "interview-1",
      question: "Solve two sum",
      body: "**Approach** Use a map.\n```python\ndef solve():\n    pass\n```",
      answer_type: "coding",
      depth: "concise",
      expansion_options: ["code", "edge_cases"],
    };
    const props = setup({ items: [interviewItem], interviewMode: true });

    expect(screen.getByText("Approach")).toBeInTheDocument();
    expect(screen.getByText(/def solve/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/paste an interview prompt/i)).toHaveAttribute(
      "maxlength",
      "6000",
    );
    fireEvent.click(screen.getByRole("button", { name: "Full solution" }));
    expect(props.onAsk).toHaveBeenCalledWith("Solve two sum", {
      intent: "expand",
      parentItemId: "interview-1",
      focus: "code",
    });
  });
});
