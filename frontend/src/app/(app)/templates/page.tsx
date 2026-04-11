"use client";

import { useState } from "react";
import useSWR from "swr";
import { Plus, Edit2, Trash2, FileText, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import type { Template } from "@/lib/types";
import { TemplateEditor } from "@/components/templates/TemplateEditor";

// ---------------------------------------------------------------------------
// Category styles
// ---------------------------------------------------------------------------

const CATEGORY_LABEL: Record<string, string> = {
  reply: "Reply",
  outreach: "Outreach",
  follow_up: "Follow-up",
  other: "Other",
};

const CATEGORY_COLOR: Record<string, string> = {
  reply: "bg-blue-500/20 text-blue-300 ring-1 ring-blue-500/30",
  outreach: "bg-emerald-500/20 text-emerald-300 ring-1 ring-emerald-500/30",
  follow_up: "bg-amber-500/20 text-amber-300 ring-1 ring-amber-500/30",
  other: "bg-slate-500/20 text-slate-300 ring-1 ring-slate-500/30",
};

// ---------------------------------------------------------------------------
// Delete confirm dialog
// ---------------------------------------------------------------------------

function DeleteDialog({
  template,
  deleting,
  onConfirm,
  onCancel,
}: {
  template: Template;
  deleting: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-2xl">
        <h2 className="text-base font-semibold text-slate-100">
          Delete template?
        </h2>
        <p className="mt-2 text-sm text-slate-400">
          <span className="font-medium text-slate-200">
            &ldquo;{template.name}&rdquo;
          </span>{" "}
          will be permanently deleted. This cannot be undone.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={onCancel}
            className="rounded-lg border border-slate-600 px-4 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={deleting}
            className="flex items-center gap-1.5 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:opacity-50"
          >
            {deleting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type EditorState = "closed" | "new" | Template;

export default function TemplatesPage() {
  const {
    data,
    isLoading,
    error,
    mutate,
  } = useSWR<{ templates: Template[] }>("/templates", (url: string) =>
    api.get<{ templates: Template[] }>(url),
  );

  const [editorState, setEditorState] = useState<EditorState>("closed");
  const [deleteTarget, setDeleteTarget] = useState<Template | null>(null);
  const [deleting, setDeleting] = useState(false);

  async function handleDelete() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await api.del(`/templates/${deleteTarget.id}`);
      await mutate();
      setDeleteTarget(null);
    } catch {
      // keep dialog open so user sees the button return to normal
    } finally {
      setDeleting(false);
    }
  }

  const editingTemplate =
    editorState !== "closed" && editorState !== "new"
      ? (editorState as Template)
      : undefined;

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold text-slate-100">Templates</h1>
        <button
          onClick={() => setEditorState("new")}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
        >
          <Plus className="h-4 w-4" />
          New template
        </button>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-24 animate-pulse rounded-lg border border-slate-700/50 bg-slate-800/40"
              style={{ animationDelay: `${i * 60}ms` }}
            />
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <p className="text-sm text-red-400">
          Failed to load templates: {(error as Error).message}
        </p>
      )}

      {/* Empty state */}
      {!isLoading && !error && (!data?.templates || data.templates.length === 0) && (
        <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
          <FileText className="h-12 w-12 text-slate-700" />
          <p className="text-base font-medium text-slate-300">
            No templates yet
          </p>
          <p className="text-sm text-slate-500">
            Create one to speed up common replies
          </p>
          <button
            onClick={() => setEditorState("new")}
            className="mt-2 flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500"
          >
            <Plus className="h-4 w-4" />
            Create your first template
          </button>
        </div>
      )}

      {/* Template list */}
      {!isLoading && data?.templates && data.templates.length > 0 && (
        <div className="space-y-3 pb-6">
          {data.templates.map((tpl) => {
            const catKey = tpl.category ?? "other";
            const catColor = CATEGORY_COLOR[catKey] ?? CATEGORY_COLOR.other;
            const bodyPreview =
              tpl.body_template.slice(0, 120) +
              (tpl.body_template.length > 120 ? "…" : "");

            return (
              <div
                key={tpl.id}
                className="flex items-start gap-3 rounded-lg border border-slate-700/50 bg-slate-800/40 p-4 transition-colors hover:bg-slate-800/60"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <p className="font-semibold text-slate-100">{tpl.name}</p>
                    {tpl.category && (
                      <span
                        className={`rounded px-1.5 py-0.5 text-xs font-medium ${catColor}`}
                      >
                        {CATEGORY_LABEL[catKey] ?? catKey}
                      </span>
                    )}
                  </div>
                  {tpl.subject_template && (
                    <p className="mt-0.5 text-xs text-slate-400">
                      Subject: {tpl.subject_template}
                    </p>
                  )}
                  <p className="mt-1 text-xs text-slate-500">{bodyPreview}</p>
                </div>

                <div className="flex shrink-0 gap-1">
                  <button
                    onClick={() => setEditorState(tpl)}
                    aria-label={`Edit ${tpl.name}`}
                    className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-slate-700 hover:text-slate-100"
                  >
                    <Edit2 className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => setDeleteTarget(tpl)}
                    aria-label={`Delete ${tpl.name}`}
                    className="rounded-md p-1.5 text-slate-400 transition-colors hover:bg-red-500/10 hover:text-red-400"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Template editor modal */}
      {editorState !== "closed" && (
        <TemplateEditor
          template={editingTemplate}
          onClose={() => setEditorState("closed")}
          onSave={() => {
            mutate();
            setEditorState("closed");
          }}
        />
      )}

      {/* Delete confirm dialog */}
      {deleteTarget && (
        <DeleteDialog
          template={deleteTarget}
          deleting={deleting}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
