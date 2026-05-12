"use client";

import { useEffect, useState } from "react";

let disconnected = false;
const listeners = new Set<(value: boolean) => void>();

function emit(value: boolean): void {
  if (disconnected === value) return;
  disconnected = value;
  for (const fn of listeners) fn(value);
}

export function markGoogleDisconnected(): void {
  emit(true);
}

export function clearGoogleDisconnected(): void {
  emit(false);
}

export function useGoogleDisconnected(): boolean {
  const [value, setValue] = useState(disconnected);
  useEffect(() => {
    listeners.add(setValue);
    setValue(disconnected);
    return () => {
      listeners.delete(setValue);
    };
  }, []);
  return value;
}
