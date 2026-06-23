"use client";

import { useState } from "react";
import { X } from "lucide-react";
import type { JobStatus } from "@/lib/types";
import { ALL_STATUSES } from "./constants";

interface JobFormValues {
  company: string;
  role_title: string;
  location: string;
  job_url: string;
  status: JobStatus;
  contact_name: string;
  contact_email: string;
  compensation: string;
  notes: string;
}

const EMPTY: JobFormValues = {
  company: "",
  role_title: "",
  location: "",
  job_url: "",
  status: "applied",
  contact_name: "",
  contact_email: "",
  compensation: "",
  notes: "",
};

export function AddJobModal({
  onClose,
  onSubmit,
}: {
  onClose: () => void;
  onSubmit: (values: JobFormValues) => Promise<void>;
}) {
  const [values, setValues] = useState<JobFormValues>(EMPTY);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof JobFormValues>(key: K, v: JobFormValues[K]) {
    setValues((prev) => ({ ...prev, [key]: v }));
  }

  async function handleSubmit() {
    if (!values.company.trim() || !values.role_title.trim()) {
      setError("Company and role are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onSubmit(values);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add job.");
    } finally {
      setBusy(false);
    }
  }

  const input =
    "w-full rounded-md border border-slate-600 bg-slate-800/60 px-3 py-2 text-sm text-slate-100 placeholder-slate-500 focus:border-indigo-500 focus:outline-none";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-xl border border-slate-700 bg-slate-900 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-100">Add job</h2>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="sm:col-span-1">
            <label className="mb-1 block text-xs text-slate-400">Company *</label>
            <input className={input} value={values.company} onChange={(e) => set("company", e.target.value)} />
          </div>
          <div className="sm:col-span-1">
            <label className="mb-1 block text-xs text-slate-400">Role *</label>
            <input className={input} value={values.role_title} onChange={(e) => set("role_title", e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Location</label>
            <input className={input} value={values.location} onChange={(e) => set("location", e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Stage</label>
            <select
              className={input}
              value={values.status}
              onChange={(e) => set("status", e.target.value as JobStatus)}
            >
              {ALL_STATUSES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-slate-400">Job URL</label>
            <input className={input} value={values.job_url} onChange={(e) => set("job_url", e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Contact name</label>
            <input className={input} value={values.contact_name} onChange={(e) => set("contact_name", e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400">Contact email</label>
            <input className={input} value={values.contact_email} onChange={(e) => set("contact_email", e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-slate-400">Compensation</label>
            <input className={input} value={values.compensation} onChange={(e) => set("compensation", e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs text-slate-400">Notes</label>
            <textarea
              className={`${input} min-h-[64px]`}
              value={values.notes}
              onChange={(e) => set("notes", e.target.value)}
            />
          </div>
        </div>

        {error && <p className="mt-3 text-sm text-red-400">{error}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="rounded-md bg-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-600"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={busy}
            className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy ? "Adding…" : "Add job"}
          </button>
        </div>
      </div>
    </div>
  );
}
