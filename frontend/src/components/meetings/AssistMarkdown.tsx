import { Fragment, type ReactNode } from "react";

function inline(text: string): ReactNode[] {
  return text
    .split(/(`[^`]+`|\*\*[^*]+\*\*)/g)
    .filter(Boolean)
    .map((part, index) => {
      if (part.startsWith("`") && part.endsWith("`")) {
        return (
          <code key={index} className="rounded bg-slate-800 px-1 py-0.5 font-mono text-xs text-indigo-200">
            {part.slice(1, -1)}
          </code>
        );
      }
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={index} className="font-semibold text-slate-200">{part.slice(2, -2)}</strong>;
      }
      return <Fragment key={index}>{part}</Fragment>;
    });
}

/** Small, deliberately HTML-free Markdown renderer for live-assist cards. */
export function AssistMarkdown({ body }: { body: string }) {
  // The trailing alternative catches a fence the model never closed (a cut-off
  // answer). Without it the whole remainder falls through to the line renderer,
  // which trims each line and destroys the indentation of the code.
  const blocks = body.split(/(```[\s\S]*?```|```[\s\S]*$)/g).filter(Boolean);
  return (
    <div className="mt-1 space-y-1.5 text-sm leading-snug text-slate-400">
      {blocks.map((block, blockIndex) => {
        if (block.startsWith("```")) {
          const closed = block.length > 3 && block.endsWith("```");
          const raw = closed ? block.slice(3, -3) : block.slice(3);
          const newline = raw.indexOf("\n");
          const language = newline >= 0 ? raw.slice(0, newline).trim() : "";
          // Strip surrounding blank lines only — trimming would eat the first
          // line's indentation while leaving every other line indented.
          const code = (newline >= 0 ? raw.slice(newline + 1) : raw)
            .replace(/^\n+/, "")
            .replace(/\s+$/, "");
          if (!code) return null;
          return (
            <div key={blockIndex} className="overflow-hidden rounded-md border border-slate-700/60 bg-slate-950/70">
              {language && <div className="border-b border-slate-800 px-2 py-1 text-[10px] uppercase text-slate-500">{language}</div>}
              <pre className="max-h-72 overflow-auto p-2 text-xs leading-relaxed text-slate-300">
                <code>{code}</code>
              </pre>
            </div>
          );
        }
        return block.split("\n").map((line, lineIndex) => {
          const trimmed = line.trim();
          if (!trimmed) return null;
          const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
          if (heading) {
            return <p key={`${blockIndex}-${lineIndex}`} className="pt-1 font-semibold text-slate-200">{inline(heading[2])}</p>;
          }
          const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
          const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
          if (bullet || ordered) {
            return (
              <div key={`${blockIndex}-${lineIndex}`} className="flex gap-2 pl-1">
                <span className="text-slate-600">{ordered ? `${trimmed.split(".")[0]}.` : "•"}</span>
                <span>{inline((bullet ?? ordered)![1])}</span>
              </div>
            );
          }
          return <p key={`${blockIndex}-${lineIndex}`}>{inline(trimmed)}</p>;
        });
      })}
    </div>
  );
}
