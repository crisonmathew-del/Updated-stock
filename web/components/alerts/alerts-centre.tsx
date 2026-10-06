"use client";

import * as Tabs from "@radix-ui/react-tabs";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AlertChannels } from "@/components/alerts/channels";
import { AlertHistory } from "@/components/alerts/history";
import { AlertRules } from "@/components/alerts/rules";
import { useLive } from "@/stores/live";

const TABS = [
  { value: "history", label: "History" },
  { value: "rules", label: "Rules" },
  { value: "channels", label: "Channels" },
] as const;
type Tab = (typeof TABS)[number]["value"];

const TRIGGER =
  "border-b-2 border-transparent px-3 py-2 text-sm text-muted hover:text-foreground data-[state=active]:border-tide data-[state=active]:text-foreground";

/** The alerts centre (spec §8.7): every alert with what each channel did, your rules, and how
 * alerts reach you. The tab is in the URL (?tab=rules) so links can open it; ?symbol= fills the
 * rule builder's stock. */
export function AlertsCentre() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const connected = useLive((s) => s.connected);
  const asked = params.get("tab");
  const tab: Tab = TABS.some((t) => t.value === asked) ? (asked as Tab) : "history";

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6">
      <div className="flex items-baseline justify-between gap-3">
        <h1 className="text-lg font-semibold">Alerts</h1>
        <p className="text-xs text-muted" role="status">
          <span aria-hidden className={connected ? "text-rise" : "text-muted"}>
            ●
          </span>{" "}
          {connected ? "Live: new alerts appear as they happen" : "Not connected to live updates"}
        </p>
      </div>
      <Tabs.Root
        value={tab}
        onValueChange={(value) => {
          const next = new URLSearchParams(params.toString());
          next.set("tab", value);
          router.replace(`${pathname}?${next.toString()}`, { scroll: false });
        }}
      >
        <Tabs.List aria-label="Alerts" className="mb-4 flex border-b border-border">
          {TABS.map((t) => (
            <Tabs.Trigger key={t.value} value={t.value} className={TRIGGER}>
              {t.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value="history">
          <AlertHistory />
        </Tabs.Content>
        <Tabs.Content value="rules">
          <AlertRules initialSymbol={(params.get("symbol") ?? "").toUpperCase()} />
        </Tabs.Content>
        <Tabs.Content value="channels">
          <AlertChannels />
        </Tabs.Content>
      </Tabs.Root>
    </main>
  );
}
