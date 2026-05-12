"use client";

import { usePathname } from "next/navigation";
import Link from "next/link";
import {
  Home,
  Inbox,
  Mic,
  Calendar,
  Clock,
  Target,
  Users,
  FileText,
  Settings,
  Bell,
} from "lucide-react";
import { Sidebar } from "@/components/layout/Sidebar";
import { GoogleDisconnectedBanner } from "@/components/layout/GoogleDisconnectedBanner";
import { VoiceProvider, useVoiceContext } from "@/components/felix/VoiceContext";
import { VoiceModal } from "@/components/felix/VoiceModal";
import { FloatingVoiceFab } from "@/components/felix/FloatingVoiceFab";

interface AppShellProps {
  userEmail: string;
  displayName: string | null;
  children: React.ReactNode;
}

const PAGE_TITLES: Record<string, string> = {
  "/home": "Home",
  "/dashboard": "Dashboard",
  "/inbox": "Inbox",
  "/calendar": "Calendar",
  "/follow-ups": "Follow-ups",
  "/commitments": "Commitments",
  "/contacts": "Contacts",
  "/templates": "Templates",
  "/settings": "Settings",
  "/briefing": "Briefing",
};

const MOBILE_NAV = [
  { href: "/home", icon: Home, label: "Home" },
  { href: "/inbox", icon: Inbox, label: "Inbox" },
  { href: "/briefing", icon: Mic, label: "Briefing" },
  { href: "/calendar", icon: Calendar, label: "Calendar" },
  { href: "/follow-ups", icon: Clock, label: "Follow-ups" },
  { href: "/commitments", icon: Target, label: "Commitments" },
  { href: "/contacts", icon: Users, label: "Contacts" },
  { href: "/templates", icon: FileText, label: "Templates" },
  { href: "/settings", icon: Settings, label: "Settings" },
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

/**
 * Inner shell — runs inside the VoiceProvider so it can read modal state.
 */
function ShellInner({ userEmail, displayName, children }: AppShellProps) {
  const pathname = usePathname();
  const { modalOpen } = useVoiceContext();

  const title = getPageTitle(pathname);
  const avatarText = initials(displayName, userEmail);

  return (
    <div className="flex h-screen overflow-hidden bg-[#080f1e] text-slate-100">
      {/* Desktop collapsible sidebar */}
      <Sidebar userEmail={userEmail} displayName={displayName} />

      {/* Main area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-white/[0.04] bg-[#080f1e] px-4">
          <div className="flex items-center gap-3">
            <h1 className="text-base font-semibold text-slate-100">{title}</h1>
          </div>

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

        <GoogleDisconnectedBanner />

        {/* Page content — extra bottom padding on mobile so the bottom nav doesn't overlap */}
        <main className="flex-1 overflow-y-auto pb-20 md:pb-0">{children}</main>
      </div>

      {/* Mobile bottom nav bar — icons only, scrolls horizontally if cramped */}
      <nav
        className="fixed bottom-0 left-0 right-0 z-30 flex items-center justify-around overflow-x-auto border-t border-white/[0.04] bg-[#0d1526] py-2 md:hidden"
        aria-label="Mobile navigation"
      >
        {MOBILE_NAV.map(({ href, icon: Icon, label }) => {
          const active = pathname === href || pathname.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={[
                "flex shrink-0 flex-col items-center justify-center px-3 py-1 transition-colors",
                active ? "text-indigo-400" : "text-slate-500",
              ].join(" ")}
              aria-label={label}
            >
              <Icon className="h-5 w-5" />
            </Link>
          );
        })}
      </nav>

      {/* Floating Voice Orb FAB — present on every page */}
      <FloatingVoiceFab />

      {/* Full-screen Voice Modal — shared session via context */}
      {modalOpen && <VoiceModal />}
    </div>
  );
}

export function AppShell(props: AppShellProps) {
  return (
    <VoiceProvider>
      <ShellInner {...props} />
    </VoiceProvider>
  );
}
