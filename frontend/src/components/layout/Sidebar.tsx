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
import { useVoice } from "@/hooks/useVoice";
import { useUnreadCounts } from "@/hooks/useUnreadCounts";
import { useEffect, useState } from "react";

interface NavItem {
  href: string;
  label: string;
  icon: React.ElementType;
  badge?: number;
}

interface SidebarProps {
  userEmail: string;
}

export function Sidebar({ userEmail }: SidebarProps) {
  const pathname = usePathname();
  const router = useRouter();
  const { actionRequired, overdueFollowups } = useUnreadCounts();
  const [token, setToken] = useState<string | null>(null);
  const { state: voiceState, start, stop } = useVoice(token);

  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setToken(session?.access_token ?? null);
    });

    const { data: listener } = supabase.auth.onAuthStateChange(
      (_event, session) => {
        setToken(session?.access_token ?? null);
      },
    );
    return () => listener.subscription.unsubscribe();
  }, []);

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

  function handleVoiceClick() {
    if (voiceState === "idle" || voiceState === "error") {
      start();
    } else {
      stop();
    }
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

      {/* Bottom section: VoiceOrb + user info */}
      <div className="px-5">
        <div className="mb-6 flex justify-center">
          <VoiceOrb state={voiceState} onClick={handleVoiceClick} size={64} />
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
