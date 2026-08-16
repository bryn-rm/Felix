import { render } from "@testing-library/react";
import { AssistMarkdown } from "@/components/meetings/AssistMarkdown";

describe("AssistMarkdown", () => {
  it("preserves indentation on every line of a fenced block", () => {
    const { container } = render(
      <AssistMarkdown body={"```python\n    if x:\n        return 1\n```"} />,
    );
    // Trimming the block would flush the first line left and leave the rest
    // indented — a snippet that reads as broken at a glance mid-interview.
    expect(container.querySelector("pre code")?.textContent).toBe(
      "    if x:\n        return 1",
    );
  });

  it("renders a fence the model never closed as code, not prose", () => {
    const { container } = render(
      <AssistMarkdown
        body={"**Approach** Hash map.\n```python\ndef f():\n    return 1"}
      />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("Approach");
    expect(container.querySelector("pre code")?.textContent).toBe(
      "def f():\n    return 1",
    );
  });

  it("renders headings, bullets, and inline code around a block", () => {
    const { container } = render(
      <AssistMarkdown
        body={"## Clarify\n- Sorted input? (assume no)\n\n```js\nlet a = 1;\n```"}
      />,
    );
    expect(container.textContent).toContain("Clarify");
    expect(container.textContent).toContain("Sorted input? (assume no)");
    expect(container.querySelector("pre code")?.textContent).toBe("let a = 1;");
  });
});
