/**
 * Tests for the Meeting Capture enhanced-notes rendering (Phase 8).
 *
 * The exit-criteria contract: the user's own notes render verbatim and visually
 * distinct (bright) from AI-added context (grey).
 */
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { EnhancedNotes } from "@/components/meetings/EnhancedNotes";
import type { EnhancedNote } from "@/lib/types";

describe("EnhancedNotes", () => {
  it("renders user blocks bright and ai blocks grey", () => {
    const notes: EnhancedNote[] = [
      { origin: "user", text: "ship the billing fix !!" },
      { origin: "ai", text: "Refers to the duplicate-charge bug." },
    ];
    render(<EnhancedNotes notes={notes} />);

    const userBlock = screen.getByText("ship the billing fix !!");
    const aiBlock = screen.getByText("Refers to the duplicate-charge bug.");

    // User text is preserved verbatim and styled bright; AI text is grey.
    expect(userBlock).toHaveClass("text-slate-100");
    expect(userBlock).toHaveClass("font-medium");
    expect(aiBlock).toHaveClass("text-slate-400");
  });

  it("shows an empty state when there are no notes", () => {
    render(<EnhancedNotes notes={[]} />);
    expect(
      screen.getByText("No enhanced notes for this meeting."),
    ).toBeInTheDocument();
  });
});
