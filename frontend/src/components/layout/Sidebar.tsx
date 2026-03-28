"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  Inbox,
  Calendar,
  Clock,
  Users,
  FileText,
  Settings,
  LogOut,
} from "lucide-react";
import { supabase } from "@/lib/supabase";
import { VoiceOrb } from "@/components/felix/VoiceOrb";
import { useUnreadCounts } from "@/hooks/useUnreadCounts";
import type { VoiceState } from "@/hooks/useVoice";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  badge?: number;
}

interface SidebarProps {
  userEmail: string;
  /**
   * Current voice session state — mirrored from the VoiceModal that lives in
   * AppShell. Drives the orb appearance. Defaults to "idle".
   */
  voiceState?: VoiceState;
  /**
   * Called when the user clicks the VoiceOrb. AppShell handles opening the
   * VoiceModal overlay; the actual WebSocket session lives there, not here.
   */
  onVoiceClick?: () => void;
}

export function Sidebar({
  userEmail,
  voiceState = "idle",
  onVoiceClick,
}: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { actionRequired, overdueFollowups } = useUnreadCounts();

  const navItems: NavItem[] = [
    { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
    {
      href: "/inbox",
      label: "Inbox",
      icon: Inbox,
      badge: actionRequired > 0 ? actionRequired : undefined,
    },
    { href: "/calendar", label: "Calendar", icon: Calendar },
    {
      href: "/follow-ups",
      label: "Follow-ups",
      icon: Clock,
      badge: overdueFollowups > 0 ? overdueFollowups : undefined,
    },
    { href: "/contacts", label: "Contacts", icon: Users },
    { href: "/templates", label: "Templates", icon: FileText },
    { href: "/settings", label: "Settings", icon: Settings },
  ];

  async function handleSignOut() {
    await supabase.auth.signOut();
    router.push("/login");
  }

  return (
    <div className="flex h-full flex-col justify-between py-4">
      {/* Logo */}
      <div>
        <div className="px-5 pb-6 pt-2">
          <span className="text-xl font-bold tracking-tight text-slate-100">
            Felix
          </span>
        </div>

        {/* Nav links */}
        <nav className="space-y-0.5 px-3">
          {navItems.map(({ href, label, icon: Icon, badge }) => {
            const active =
              pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={[
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  active
                    ? "border-l-2 border-indigo-500 bg-indigo-600/20 pl-[10px] text-slate-100"
                    : "border-l-2 border-transparent text-slate-400 hover:bg-slate-700/50 hover:text-slate-100",
                ].join(" ")}
              >
                <Icon className="h-4 w-4 shrink-0" />
                <span className="flex-1">{label}</span>
                {badge !== undefined && (
                  <span className="rounded-full bg-indigo-600 px-1.5 py-0.5 text-xs font-semibold text-white leading-none">
                    {badge > 99 ? "99+" : badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </div>

      {/* Bottom section: VoiceOrb (above) + user info (below) */}
      <div className="px-5">
        {/* VoiceOrb — positioned at the bottom of nav links, above user info */}
        <div className="mb-6 flex justify-center">
          <VoiceOrb
            state={voiceState}
            onClick={onVoiceClick ?? (() => {})}
            size={64}
          />
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-slate-700 pt-4">
          <span className="truncate text-xs text-slate-400">{userEmail}</span>
          <button
            onClick={handleSignOut}
            title="Sign out"
            className="shrink-0 rounded p-1 text-slate-400 transition-colors hover:bg-slate-700 hover:text-slate-100"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </div>
  );
}
