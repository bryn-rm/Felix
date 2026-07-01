"use client";

/**
 * Claude-style collapsible sidebar.
 *
 * - Collapsed (icon-only, ~56px) by default.
 * - Expands to ~240px on hover (after 150ms delay) or click.
 * - Pin button (chevron) toggles a sticky-open mode.
 * - 200ms ease CSS transition.
 *
 * Mobile bottom tab bar is rendered separately by AppShell.
 */

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import {
  Home,
  Inbox,
  Mic,
  Calendar,
  Clock,
  Target,
  Briefcase,
  Radio,
  Users,
  FileText,
  Settings,
  LogOut,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";
import useSWR, { useSWRConfig } from "swr";
import { supabase } from "@/lib/supabase";
import { api } from "@/lib/api";
import { clearAllSWR } from "@/components/auth/AuthSync";
import { useUnreadCounts } from "@/hooks/useUnreadCounts";
import type { Settings as UserSettings } from "@/lib/types";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  badge?: number;
}

interface SidebarProps {
  userEmail: string;
  displayName: string | null;
}

const HOVER_DELAY_MS = 150;

function getInitials(displayName: string | null, email: string): string {
  if (displayName) {
    const parts = displayName.trim().split(/\s+/);
    if (parts.length >= 2) {
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    }
    return parts[0].slice(0, 2).toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}

export function Sidebar({ userEmail, displayName }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { actionRequired, overdueFollowups } = useUnreadCounts();
  const { mutate } = useSWRConfig();
  // Fail closed: the Jobs item only appears when job_search_mode is explicitly on.
  const { data: settings } = useSWR<UserSettings>("/settings", (url: string) =>
    api.get<UserSettings>(url),
  );
  const jobSearchEnabled = settings?.job_search_mode === true;
  // Fail closed: the Meetings item only appears when meeting_capture_mode is on.
  const meetingCaptureEnabled = settings?.meeting_capture_mode === true;

  const [pinned, setPinned] = useState(false);
  const [hoverExpanded, setHoverExpanded] = useState(false);
  const hoverTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const expanded = pinned || hoverExpanded;

  useEffect(() => {
    return () => {
      if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    };
  }, []);

  function handleMouseEnter() {
    if (pinned) return;
    if (hoverTimerRef.current) clearTimeout(hoverTimerRef.current);
    hoverTimerRef.current = setTimeout(() => {
      setHoverExpanded(true);
    }, HOVER_DELAY_MS);
  }

  function handleMouseLeave() {
    if (hoverTimerRef.current) {
      clearTimeout(hoverTimerRef.current);
      hoverTimerRef.current = null;
    }
    setHoverExpanded(false);
  }

  const navItems: NavItem[] = [
    { href: "/home", label: "Home", icon: Home },
    {
      href: "/inbox",
      label: "Inbox",
      icon: Inbox,
      badge: actionRequired > 0 ? actionRequired : undefined,
    },
    { href: "/briefing", label: "Briefing", icon: Mic },
    { href: "/calendar", label: "Calendar", icon: Calendar },
    {
      href: "/follow-ups",
      label: "Follow-ups",
      icon: Clock,
      badge: overdueFollowups > 0 ? overdueFollowups : undefined,
    },
    { href: "/commitments", label: "Commitments", icon: Target },
    ...(jobSearchEnabled
      ? [{ href: "/jobs", label: "Jobs", icon: Briefcase } as NavItem]
      : []),
    ...(meetingCaptureEnabled
      ? [{ href: "/meetings", label: "Meetings", icon: Radio } as NavItem]
      : []),
    { href: "/contacts", label: "Contacts", icon: Users },
    { href: "/templates", label: "Templates", icon: FileText },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  async function handleSignOut() {
    await clearAllSWR(mutate);
    await supabase.auth.signOut();
    router.push("/login");
  }

  const initials = getInitials(displayName, userEmail);

  return (
    <aside
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      style={{ width: expanded ? 240 : 56 }}
      className={[
        "hidden md:flex md:flex-col h-full shrink-0 bg-[#0d1526] border-r border-white/[0.04]",
        "transition-[width] duration-200 ease-out overflow-hidden",
      ].join(" ")}
      aria-label="Primary navigation"
    >
      {/* Top — logo + pin toggle */}
      <div className="flex items-center justify-between h-14 px-3 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <Image
            src="/sidebar-icon-32.png"
            alt="Felix"
            width={32}
            height={32}
            className="h-8 w-8 shrink-0 rounded-md"
          />
          {expanded && (
            <span className="truncate text-sm font-semibold tracking-tight text-slate-100">
              Felix
            </span>
          )}
        </div>
        {expanded && (
          <button
            onClick={() => setPinned((v) => !v)}
            className="rounded p-1 text-slate-500 hover:bg-slate-700/50 hover:text-slate-200 transition-colors"
            aria-label={pinned ? "Unpin sidebar" : "Pin sidebar"}
            title={pinned ? "Unpin" : "Pin open"}
          >
            {pinned ? (
              <ChevronsLeft className="h-4 w-4" />
            ) : (
              <ChevronsRight className="h-4 w-4" />
            )}
          </button>
        )}
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-2 space-y-0.5">
        {navItems.map(({ href, label, icon: Icon, badge }) => {
          const active =
            pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              title={expanded ? undefined : label}
              className={[
                "group relative flex items-center h-10 rounded-md text-sm font-medium",
                "transition-colors",
                active
                  ? "bg-indigo-600/20 text-slate-100"
                  : "text-slate-400 hover:bg-slate-700/40 hover:text-slate-100",
              ].join(" ")}
            >
              <span className="flex h-10 w-10 shrink-0 items-center justify-center">
                <Icon className="h-[18px] w-[18px]" />
              </span>
              {expanded && (
                <span className="flex-1 truncate pr-2">{label}</span>
              )}
              {badge !== undefined &&
                (expanded ? (
                  <span className="mr-2 rounded-full bg-indigo-600 px-1.5 py-0.5 text-[10px] font-semibold leading-none text-white">
                    {badge > 99 ? "99+" : badge}
                  </span>
                ) : (
                  <span
                    className="absolute top-1.5 right-1.5 h-2 w-2 rounded-full bg-indigo-500 ring-2 ring-[#0d1526]"
                    aria-label={`${badge} unread`}
                  />
                ))}
              {active && (
                <span className="absolute left-0 top-1/2 h-5 w-0.5 -translate-y-1/2 rounded-r bg-indigo-400" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Bottom — user + sign out */}
      <div className="shrink-0 border-t border-white/[0.04] p-2">
        <div className="flex items-center h-10 gap-2">
          <div
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-indigo-600 text-[11px] font-bold text-white"
            title={displayName ?? userEmail}
          >
            {initials}
          </div>
          {expanded && (
            <span className="flex-1 truncate text-xs text-slate-400">
              {userEmail}
            </span>
          )}
          {expanded && (
            <button
              onClick={handleSignOut}
              title="Sign out"
              className="shrink-0 rounded p-1.5 text-slate-500 transition-colors hover:bg-slate-700/50 hover:text-slate-100"
              aria-label="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}
