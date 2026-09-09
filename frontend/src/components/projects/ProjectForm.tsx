"use client";

import { useState } from "react";
import type { ProjectValues } from "@/hooks/useProjects";
import { ProjectDialog, projectButton, projectInput } from "./ProjectDialog";

export function ProjectForm({ initial, onSubmit, onClose }: {
  initial?: ProjectValues; onSubmit: (values: ProjectValues) => Promise<void>; onClose: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [target, setTarget] = useState(initial?.target_date ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <ProjectDialog title={initial ? "Edit project" : "Create project"} onClose={() => { if (!busy) onClose(); }}>
      <form className="space-y-4" onSubmit={async (event) => {
        event.preventDefault();
        if (!name.trim()) { setError("Enter a project name."); return; }
        setBusy(true); setError("");
        try {
          await onSubmit({ name: name.trim(), description: description.trim(), target_date: target || null });
          onClose();
        } catch (e) { setError(e instanceof Error ? e.message : "Could not save project."); }
        finally { setBusy(false); }
      }}>
        <label className="block text-sm">Project name
          <input required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} className={projectInput} />
        </label>
        <label className="block text-sm">Description
          <textarea maxLength={10000} rows={4} value={description} onChange={(e) => setDescription(e.target.value)} className={projectInput} />
        </label>
        <label className="block text-sm">Target date (optional)
          <input type="date" value={target} onChange={(e) => setTarget(e.target.value)} className={projectInput} />
        </label>
        {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
        <button disabled={busy} className={projectButton}>{busy ? "Saving…" : initial ? "Save changes" : "Create project"}</button>
      </form>
    </ProjectDialog>
  );
}
