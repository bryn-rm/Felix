import "@testing-library/jest-dom";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";

import {
  NotesEditor,
  type NotesEditorHandle,
} from "@/components/meetings/NotesEditor";

describe("NotesEditor", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it("flushes pending notes immediately without saving them twice", async () => {
    const onSave = jest.fn().mockResolvedValue(undefined);
    const ref = createRef<NotesEditorHandle>();
    const { unmount } = render(
      <NotesEditor ref={ref} initialValue="" onSave={onSave} />,
    );

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "typed right before stopping" },
    });

    await act(async () => {
      await ref.current?.flush();
    });

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onSave).toHaveBeenCalledWith("typed right before stopping");

    act(() => {
      jest.advanceTimersByTime(800);
    });
    unmount();

    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("keeps a failed flush pending so it can be retried", async () => {
    const onSave = jest
      .fn()
      .mockRejectedValueOnce(new Error("save failed"))
      .mockResolvedValueOnce(undefined);
    const ref = createRef<NotesEditorHandle>();
    render(<NotesEditor ref={ref} initialValue="" onSave={onSave} />);

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "retry me" },
    });

    await expect(ref.current?.flush()).rejects.toThrow("save failed");

    await act(async () => {
      await ref.current?.flush();
    });

    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith("retry me");
  });
});
