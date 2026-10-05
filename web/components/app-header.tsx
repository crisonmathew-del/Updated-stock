"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Status" },
  { href: "/admin/data", label: "Data" },
  { href: "/admin/inspect", label: "Inspect" },
  { href: "/admin/patterns", label: "Patterns" },
  { href: "/admin/setups", label: "Setups" },
  { href: "/admin/signals", label: "Signals" },
];

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const me = useQuery({
    queryKey: ["auth", "me"],
    queryFn: () => api.get<{ email: string }>("/api/auth/me"),
    staleTime: 5 * 60_000,
  });

  async function signOut() {
    await api.post("/api/auth/logout").catch(() => undefined);
    router.replace("/login");
  }

  return (
    <header className="border-b border-border">
      <div className="mx-auto flex max-w-5xl items-center gap-6 px-6 py-3">
        <span className="font-semibold tracking-tight">Breakout</span>
        <nav className="flex gap-4 text-sm">
          {LINKS.map(({ href, label }) => (
            <Link
              key={href}
              href={href}
              aria-current={pathname === href ? "page" : undefined}
              className={cn(
                pathname === href ? "text-foreground" : "text-muted hover:text-foreground",
              )}
            >
              {label}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-3 text-sm text-muted">
          {me.data && <span>{me.data.email}</span>}
          <button
            type="button"
            onClick={signOut}
            className="underline-offset-2 hover:text-foreground hover:underline"
          >
            Sign out
          </button>
        </div>
      </div>
    </header>
  );
}
