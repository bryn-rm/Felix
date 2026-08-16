import {
  assistModeLabel,
  resolveAssistMeetingMode,
  usesCandidateInterviewAssist,
} from "@/components/meetings/constants";

/**
 * These mirror backend/app/models/meeting.py::resolve_assist_meeting_mode.
 * The cases below are the same ones test_live_assist.py pins server-side — if
 * one side changes, both suites should move together.
 */
type Mode = Parameters<typeof resolveAssistMeetingMode>[0];

const meeting = (over: Partial<NonNullable<Mode>> = {}) =>
  ({ meeting_type: null, user_role: null, template: "general", ...over }) as Mode;

describe("assist meeting mode", () => {
  it.each([
    [meeting({ meeting_type: "general", template: "interview" }), "general"],
    [meeting({ meeting_type: "interview", user_role: "candidate" }), "interview_candidate"],
    [meeting({ meeting_type: "interview", user_role: "interviewer" }), "interview_interviewer"],
    [meeting({ template: "interview" }), "legacy_interview"],
    [meeting(), "general"],
    [null, "general"],
  ])("resolves %#", (input, expected) => {
    expect(resolveAssistMeetingMode(input)).toBe(expected);
  });

  it("fails closed on an unrecognised meeting_type rather than trusting the template", () => {
    // Matches the backend: only a NULL meeting_type marks a legacy row, so an
    // unexpected value must not inherit the template's interview behaviour.
    const odd = { meeting_type: "Interview", user_role: null, template: "interview" };
    expect(resolveAssistMeetingMode(odd as unknown as Mode)).toBe("general");
    expect(usesCandidateInterviewAssist(odd as unknown as Mode)).toBe(false);
  });

  it("gates candidate assist on the user being the one interviewed", () => {
    expect(usesCandidateInterviewAssist(
      meeting({ meeting_type: "interview", user_role: "candidate" }),
    )).toBe(true);
    expect(usesCandidateInterviewAssist(meeting({ template: "interview" }))).toBe(true);
    expect(usesCandidateInterviewAssist(
      meeting({ meeting_type: "interview", user_role: "interviewer" }),
    )).toBe(false);
    expect(usesCandidateInterviewAssist(meeting())).toBe(false);
  });

  it("labels every mode that changes behaviour, legacy interviews included", () => {
    // The bug this pins: a legacy interview runs candidate assist, so showing
    // no badge left the user with proactive solves and no idea why.
    expect(assistModeLabel(meeting({ template: "interview" }))).toBe("Interview");
    expect(assistModeLabel(
      meeting({ meeting_type: "interview", user_role: "candidate" }),
    )).toBe("Interview · Candidate");
    expect(assistModeLabel(
      meeting({ meeting_type: "interview", user_role: "interviewer" }),
    )).toBe("Interview · Interviewer");
    expect(assistModeLabel(meeting())).toBeNull();
  });

  it("never labels a meeting that does not run candidate assist as one that does", () => {
    for (const m of [
      meeting(),
      meeting({ meeting_type: "general", template: "interview" }),
      meeting({ meeting_type: "interview", user_role: "interviewer" }),
    ]) {
      expect(usesCandidateInterviewAssist(m)).toBe(false);
      expect(assistModeLabel(m)).not.toBe("Interview · Candidate");
    }
  });
});
