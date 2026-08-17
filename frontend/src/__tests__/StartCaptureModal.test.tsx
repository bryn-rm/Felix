import "@testing-library/jest-dom";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { StartCaptureModal } from "@/components/meetings/StartCaptureModal";


function consent() {
  fireEvent.click(screen.getByRole("checkbox"));
}


describe("StartCaptureModal meeting mode", () => {
  it("opens a manual assistant without recording consent", async () => {
    const onStart = jest.fn().mockResolvedValue(undefined);
    render(
      <StartCaptureModal
        open
        mode="manual"
        onClose={jest.fn()}
        onStart={onStart}
      />,
    );

    expect(screen.getByText("Open meeting assistant")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Open assistant" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      template: "general",
      title: null,
      meeting_type: "general",
      user_role: null,
      assistant_only: true,
    }));
  });

  it("defaults to General and sends an explicit general configuration", async () => {
    const onStart = jest.fn().mockResolvedValue(undefined);
    render(<StartCaptureModal open onClose={jest.fn()} onStart={onStart} />);

    expect(screen.getByRole("button", { name: /general standard live assist/i }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Your role")).not.toBeInTheDocument();

    consent();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      template: "general",
      title: null,
      meeting_type: "general",
      user_role: null,
    }));
  });

  it("requires a role and sends Candidate interview values", async () => {
    const onStart = jest.fn().mockResolvedValue(undefined);
    render(<StartCaptureModal open onClose={jest.fn()} onStart={onStart} />);

    fireEvent.click(screen.getByRole("button", { name: /interview role-aware/i }));
    consent();
    const continueButton = screen.getByRole("button", { name: "Continue" });
    expect(screen.getByText("Your role")).toBeInTheDocument();
    expect(continueButton).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: /candidate you are being/i }));
    expect(continueButton).toBeEnabled();
    fireEvent.click(continueButton);

    await waitFor(() => expect(onStart).toHaveBeenCalledWith({
      template: "interview",
      title: null,
      meeting_type: "interview",
      user_role: "candidate",
    }));
  });

  it("sends Interviewer and resets to General after closing", async () => {
    const onStart = jest.fn().mockResolvedValue(undefined);
    const props = { onClose: jest.fn(), onStart };
    const { rerender } = render(<StartCaptureModal open {...props} />);

    fireEvent.click(screen.getByRole("button", { name: /interview role-aware/i }));
    fireEvent.click(screen.getByRole("button", { name: /interviewer you are interviewing/i }));
    consent();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    await waitFor(() => expect(onStart).toHaveBeenCalledWith(expect.objectContaining({
      meeting_type: "interview",
      user_role: "interviewer",
    })));

    rerender(<StartCaptureModal open={false} {...props} />);
    rerender(<StartCaptureModal open {...props} />);

    expect(screen.getByRole("button", { name: /general standard live assist/i }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByText("Your role")).not.toBeInTheDocument();
  });

  it("keeps the chosen role when the selected type is clicked again", () => {
    render(<StartCaptureModal open onClose={jest.fn()} onStart={jest.fn()} />);

    const interview = screen.getByRole("button", { name: /interview role-aware/i });
    fireEvent.click(interview);
    fireEvent.click(screen.getByRole("button", { name: /candidate you are being/i }));
    consent();
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();

    // Re-clicking the already-selected tile reads as confirming the choice.
    fireEvent.click(interview);

    expect(screen.getByRole("button", { name: /candidate you are being/i }))
      .toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("switching type back to General does clear a stale role", () => {
    render(<StartCaptureModal open onClose={jest.fn()} onStart={jest.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /interview role-aware/i }));
    fireEvent.click(screen.getByRole("button", { name: /candidate you are being/i }));
    fireEvent.click(screen.getByRole("button", { name: /general standard live assist/i }));
    fireEvent.click(screen.getByRole("button", { name: /interview role-aware/i }));

    expect(screen.getByRole("button", { name: /candidate you are being/i }))
      .toHaveAttribute("aria-pressed", "false");
  });

  it("surfaces an error when starting fails instead of silently resetting", async () => {
    const onStart = jest.fn().mockRejectedValue(new Error("429"));
    render(<StartCaptureModal open onClose={jest.fn()} onStart={onStart} />);

    consent();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/could not start/i);
    expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
  });

  it("keeps an in-flight start locked across close and reopen", async () => {
    let finishStart: (() => void) | undefined;
    const onStart = jest.fn(() => new Promise<void>((resolve) => {
      finishStart = resolve;
    }));
    const onClose = jest.fn();
    const { rerender } = render(
      <StartCaptureModal open onClose={onClose} onStart={onStart} />,
    );

    consent();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(onStart).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Close" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();

    // Even an external parent close/reopen cannot clear the request lock.
    rerender(<StartCaptureModal open={false} onClose={onClose} onStart={onStart} />);
    rerender(<StartCaptureModal open onClose={onClose} onStart={onStart} />);

    const starting = screen.getByRole("button", { name: "Starting…" });
    expect(starting).toBeDisabled();
    fireEvent.click(starting);
    expect(onStart).toHaveBeenCalledTimes(1);

    await act(async () => finishStart?.());
  });
});
