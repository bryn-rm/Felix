"use client";

import { useRef, useState } from "react";
import { X, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Template, TemplateCategory } from "@/lib/types";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const CATEGORIES: ReadonlyArray<{ value: TemplateCategory; label: string }> = [
  { value: "reply", label: "Reply" },
  { value: "outreach", label: "Outreach" },
  { value: "follow_up", label: "Follow-up" },
  { value: "other", label: "Other" },
];

const PLACEHOLDERS = [
  { key: "{{name}}", desc: "Contact name" },
  { key: "{{company}}", desc: "Company name" },
  { key: "{{topic}}", desc: "Email topic" },
  { key: "{{date}}", desc: "Today's date" },
];

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ValidationErrors {
  name?: string;
  body?: string;
}

export interface TemplateEditorProps {
  /** Provide to edit an existing template; omit to create a new one. */
  template?: Template;
  onClose: () => void;
  /** Called after the API save succeeds — caller is responsible for refreshing data. */
  onSave: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function TemplateEditor({ template, onClose, onSave }: TemplateEditorProps) {
  const isEdit = !!template;

  const [name, setName] = useState(template?.name ?? "");
  const [category, setCategory] = useState<TemplateCategory>(
    template?.category ?? "reply",
  );
  const [subject, setSubject] = useState(template?.subject_template ?? "");
  const [body, setBody] = useState(template?.body_template ?? "");
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [saving, setSaving] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);

  const subjectRef = useRef<HTMLInputElement>(null);
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  // Track which field was last focused to know where to insert placeholders
  const activeField = useRef<"subject" | "body">("body");

  // ---- Placeholder insertion ----

  function insertPlaceholder(ph: string) {
    if (activeField.current === "subject") {
      const input = subjectRef.current;
      if (!input) return;
      const start = input.selectionStart ?? subject.length;
      const end = input.selectionEnd ?? subject.length;
      const next = subject.slice(0, start) + ph + subject.slice(end);
      setSubject(next);
      requestAnimationFrame(() => {
        input.focus();
        input.setSelectionRange(start + ph.length, start + ph.length);
      });
    } else {
      const ta = bodyRef.current;
      if (!ta) return;
      const start = ta.selectionStart ?? body.length;
      const end = ta.selectionEnd ?? body.length;
      const next = body.slice(0, start) + ph + body.slice(end);
      setBody(next);
      requestAnimationFrame(() => {
        ta.focus();
        ta.setSelectionRange(start + ph.length, start + ph.length);
      });
    }
  }

  // ---- Validation ----

  function validate(): boolean {
    const errs: ValidationErrors = {};
    if (!name.trim()) errs.name = "Name is required.";
    if (!body.trim()) errs.body = "Body is required.";
    else if (body.trim().length < 10)
      errs.body = "Body must be at least 10 characters.";
    setErrors(errs);
    return Object.keys(errs).length === 0;
  }

  // ---- Save ----

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    setApiError(null);
    try {
      const payload = {
        name: name.trim(),
        category,
        subject_template: subject.trim(),
        body_template: body.trim(),
      };
      if (isEdit) {
        await api.patch(`/templates/${template!.id}`, payload);
      } else {
        await api.post("/templates", payload);
      }
      onSave();
    } catch (err) {
      setApiError(
        err instanceof Error ? err.message : "Failed to save template.",
      );
    } finally {
      setSaving(false);
    }
  }

  // ---- Render ----

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-xl border border-slate-700 bg-slate-800 shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-slate-700 px-6 py-4">
          <h2 className="text-base font-semibold text-slate-100">
            {isEdit ? "Edit template" : "New template"}
          </h2>
          <button
            onClick={onClose}
            className="rounded-md p-1 text-slate-400 transition-colors hover:text-slate-100"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Form body */}
        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          {/* Name */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-400">
              Name <span className="text-red-400">*</span>
            </label>
            <input
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (errors.name) setErrors((p) => ({ ...p, name: undefined }));
              }}
              placeholder="e.g. Meeting follow-up"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            {errors.name && (
              <p className="mt-1 text-xs text-red-400">{errors.name}</p>
            )}
          </div>

          {/* Category */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-400">
              Category
            </label>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as TemplateCategory)}
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none"
            >
              {CATEGORIES.map((c) => (
                <option key={c.value} value={c.value}>
                  {c.label}
                </option>
              ))}
            </select>
          </div>

          {/* Subject template */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-400">
              Subject template{" "}
              <span className="font-normal text-slate-600">(optional)</span>
            </label>
            <input
              ref={subjectRef}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              onFocus={() => {
                activeField.current = "subject";
              }}
              placeholder="e.g. Following up on {{topic}}"
              className="w-full rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {/* Placeholder helper */}
          <div>
            <p className="mb-2 text-xs text-slate-500">
              Click to insert placeholder at cursor:
            </p>
            <div className="flex flex-wrap gap-1.5">
              {PLACEHOLDERS.map(({ key, desc }) => (
                <button
                  key={key}
                  type="button"
                  title={desc}
                  onClick={() => insertPlaceholder(key)}
                  className="rounded border border-slate-600 bg-slate-700/50 px-2 py-1 font-mono text-xs text-indigo-300 transition-colors hover:bg-slate-700 hover:text-indigo-200"
                >
                  {key}
                </button>
              ))}
            </div>
          </div>

          {/* Body */}
          <div>
            <label className="mb-1.5 block text-xs font-medium text-slate-400">
              Body <span className="text-red-400">*</span>
            </label>
            <textarea
              ref={bodyRef}
              value={body}
              onChange={(e) => {
                setBody(e.target.value);
                if (errors.body) setErrors((p) => ({ ...p, body: undefined }));
              }}
              onFocus={() => {
                activeField.current = "body";
              }}
              rows={10}
              placeholder={`Hi {{name}},\n\nI wanted to follow up on {{topic}}...`}
              className="w-full resize-none rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 font-mono text-sm text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none"
            />
            {errors.body && (
              <p className="mt-1 text-xs text-red-400">{errors.body}</p>
            )}
          </div>

          {/* API error */}
          {apiError && (
            <div className="rounded-lg bg-red-500/10 px-3 py-2 text-sm text-red-400">
              {apiError}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-3 border-t border-slate-700 px-6 py-4">
          <button
            onClick={onClose}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
          >
            {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {saving
              ? "Saving…"
              : isEdit
                ? "Save changes"
                : "Create template"}
          </button>
        </div>
      </div>
    </div>
  );
}
