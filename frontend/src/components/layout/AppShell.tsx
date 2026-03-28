"use client";

import { useState } from "react";
import { usePathname } from "next/navigation";
import Link from "next/link";
import {
  LayoutDashboard,
  Inbox,
  Calendar,
  Clock,
  Users,
  FileText,
  Settings,
  Bell,
  Menu,
  X,
} from "lucide-react";
import { Sidebar } from "@/components/layout/Sidebar";

interface AppShellProps {
  userEmail: string;
  displayName: string | null;
  children: React.ReactNode;
}

const PAGE_TITLES: Record<string, string> = {
  "/dashboard": "Dashboard",
  "/inbox": "Inbox",
  "/calendar": "Calendar",
  "/follow-ups": "Follow-ups",
  "/contacts": "Contacts",
  "/templates": "Templates",
  "/settings": "Settings",
};

const MOBILE_NAV = [
  { href: "/dashboard", icon: LayoutDashboard, label: "Dashboard" },
  { href: "/inbox", icon: Inbox, label: "Inbox" },
  { href: "/calendar", icon: Calendar, label: "Calendar" },
  { href: "/follow-ups", icon: Clock, label: "Follow-ups" },
  { href: "/contacts", icon: Users, label: "Contacts" },
];

function getPageTitle(pathname: string): string {
  for (const [prefix, title] of Object.entries(PAGE_TITLES)) {
    if (pathname === prefix || pathname.startsWith(prefix + "/")) {
      return title;
    }
  }
  return "Felix";
}

function initials(displayName: string | null, email: string): string {
  if (displayName) {
    const parts = displayName.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return parts[0].slice(0, 2).toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

export function AppShell({ userEmail, displayName, children }: AppShellProps) {
  const pathname = usePathname();
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const title = getPageTitle(pathname);
  const avatarText = initials(displayName, userEmail);

  return (
    <div className="flex h-screen overflow-hidden bg-[#0f172a] text-slate-100">
      {/* ------------------------------------------------------------------ */}
      {/* Desktop sidebar                                                      */}
      {/* ------------------------------------------------------------------ */}
      <aside className="hidden w-64 shrink-0 bg-[#1e293b] md:flex md:flex-col">
        <Sidebar userEmail={userEmail} />
      </aside>

      {/* ------------------------------------------------------------------ */}
      {/* Mobile sidebar overlay                                              */}
      {/* ------------------------------------------------------------------ */}
      {sidebarOpen && (
        <div className="fixed inset-0 z-40 md:hidden">
          {/* Backdrop */}
          <div
            className="absolute inset-0 bg-black/60"
            onClick={() => setSidebarOpen(false)}
          />
          {/* Drawer */}
          <aside className="absolute left-0 top-0 flex h-full w-64 flex-col bg-[#1e293b]">
            <div className="flex justify-end p-3">
              <button
                onClick={() => setSidebarOpen(false)}
                className="rounded p-1 text-slate-400 hover:text-slate-100"
              >
                <X className="h-5 w-5" />
              </button>
            </div>
            <Sidebar userEmail={userEmail} />
          </aside>
        </div>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* Main area                                                           */}
      {/* ------------------------------------------------------------------ */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-700/60 bg-[#0f172a] px-4">
          {/* Left: hamburger (mobile) + page title */}
          <div className="flex items-center gap-3">
            <button
              className="rounded p-1 text-slate-400 hover:text-slate-100 md:hidden"
              onClick={() => setSidebarOpen(true)}
              aria-label="Open sidebar"
            >
              <Menu className="h-5 w-5" />
            </button>
            <h1 className="text-base font-semibold text-slate-100">{title}</h1>
          </div>

          {/* Right: bell + avatar */}
          <div className="flex items-center gap-3">
            <button
              aria-label="Notifications"
              className="rounded p-1.5 text-slate-400 transition-colors hover:bg-slate-700 hover:text-slate-100"
            >
              <Bell className="h-5 w-5" />
            </button>

            <div
              className="flex h-8 w-8 items-center justify-center rounded-full bg-indigo-600 text-xs font-bold text-white"
              title={displayName ?? userEmail}
            >
              {avatarText}
            </div>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto p-6">{children}</main>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Mobile bottom nav bar                                               */}
      {/* ------------------------------------------------------------------ */}
      <nav className="fixed bottom-0 left-0 right-0 z-30 flex items-center justify-around border-t border-slate-700/60 bg-[#1e293b] py-2 md:hidden">
        {MOBILE_NAV.map(({ href, icon: Icon, label }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={[
                "flex flex-col items-center gap-0.5 px-3 py-1 text-xs transition-colors",
                active ? "text-indigo-400" : "text-slate-400",
              ].join(" ")}
              aria-label={label}
            >
              <Icon className="h-5 w-5" />
              <span>{label}</span>
            </Link>
          );
        })}
        <button
          className="flex flex-col items-center gap-0.5 px-3 py-1 text-xs text-slate-400"
          onClick={() => setSidebarOpen(true)}
          aria-label="More"
        >
          <Menu className="h-5 w-5" />
          <span>More</span>
        </button>
      </nav>
    </div>
  );
}
