import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";

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

  it("submits a trimmed question and clears the input", async () => {
    const props = setup();
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "  Who is Sarah?  " } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });

    expect(props.onAsk).toHaveBeenCalledWith("  Who is Sarah?  ");
    expect((input as HTMLInputElement).value).toBe("");
  });

  it("sends on Enter and keeps Shift+Enter as a newline", async () => {
    const props = setup();
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Who is Sarah?" } });

    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(props.onAsk).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.keyDown(input, { key: "Enter" });
    });
    expect(props.onAsk).toHaveBeenCalledWith("Who is Sarah?");
    expect((input as HTMLTextAreaElement).value).toBe("");
  });

  it("clears immediately and shows progress while an async ask is in flight", async () => {
    let resolveAsk: ((sent: boolean) => void) | undefined;
    const ask = new Promise<boolean>((resolve) => {
      resolveAsk = resolve;
    });
    setup({
      onAsk: jest.fn(() => ask),
    });
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Tell me more about that." } });

    fireEvent.keyDown(input, { key: "Enter" });

    expect((input as HTMLTextAreaElement).value).toBe("");
    expect(screen.getByRole("status")).toHaveTextContent(/asking felix/i);
    expect(screen.getByLabelText(/send question/i)).toBeDisabled();

    await act(async () => resolveAsk?.(true));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("disables submit while an ask is pending and shows errors", () => {
    setup({ askPending: true, askError: "over budget" });
    expect(screen.getByLabelText(/send question/i)).toBeDisabled();
    expect(screen.getByText("over budget")).toBeInTheDocument();
  });

  it("keeps the typed question when the send fails (e.g. reconnecting)", async () => {
    const props = setup({ onAsk: jest.fn(() => false) });
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Who is Sarah?" } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });

    expect(props.onAsk).toHaveBeenCalled();
    expect((input as HTMLInputElement).value).toBe("Who is Sarah?");
  });

  it("keeps the typed question when an async send resolves false", async () => {
    // The REST transport only learns the ask failed once the response lands, so
    // the retry-without-retyping contract has to survive a promise, not just a
    // synchronous false.
    const props = setup({ onAsk: jest.fn(async () => false) });
    const input = screen.getByLabelText(/ask felix a question/i);
    fireEvent.change(input, { target: { value: "Who is Sarah?" } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });

    expect(props.onAsk).toHaveBeenCalled();
    expect((input as HTMLTextAreaElement).value).toBe("Who is Sarah?");
  });

  it("flushes pending notes before the question goes out", async () => {
    const order: string[] = [];
    const props = setup({
      onBeforeAsk: jest.fn(async () => {
        order.push("flush");
      }),
      onAsk: jest.fn(() => {
        order.push("ask");
        return true;
      }),
    });
    fireEvent.change(screen.getByLabelText(/ask felix a question/i), {
      target: { value: "Did we agree on the price?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });

    expect(props.onBeforeAsk).toHaveBeenCalled();
    expect(order).toEqual(["flush", "ask"]);
  });

  it("still asks when the notes flush fails", async () => {
    const props = setup({
      onBeforeAsk: jest.fn(async () => {
        throw new Error("offline");
      }),
    });
    fireEvent.change(screen.getByLabelText(/ask felix a question/i), {
      target: { value: "Did we agree on the price?" },
    });
    await act(async () => {
      fireEvent.click(screen.getByLabelText(/send question/i));
    });

    expect(props.onAsk).toHaveBeenCalledWith("Did we agree on the price?");
  });

  it("renders interview markdown and requests a linked expansion", async () => {
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
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Full solution" }));
    });
    expect(props.onAsk).toHaveBeenCalledWith("Solve two sum", {
      intent: "expand",
      parentItemId: "interview-1",
      focus: "code",
    });
  });
});
