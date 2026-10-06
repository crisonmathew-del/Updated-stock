"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { RULES } from "@/components/alerts/queries";
import { Button } from "@/components/ui/button";
import { api, type AlertRule } from "@/lib/api";
import { useToasts } from "@/stores/toast";

/** Promote a saved screen to an alert: after each close, an alert for every stock that newly
 * matches it. Once on, links to the rule instead. */
export function ScreenAlertButton({ screenId, name }: { screenId: number; name: string }) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const rules = useQuery({ queryKey: RULES.key, queryFn: () => api.get<AlertRule[]>(RULES.path) });
  const existing = rules.data?.find((r) => r.scope === "screen" && r.screen_id === screenId);
  const create = useMutation({
    mutationFn: () =>
      api.post<AlertRule>("/api/alert-rules", {
        name: `New in ${name}`.slice(0, 80),
        scope: "screen",
        screen_id: screenId,
        condition: "new_match",
        channels: ["in_app", "email"],
        priority: "normal",
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: RULES.key });
      push(`You'll get an alert after the close when a stock newly matches "${name}".`);
    },
    onError: (error) => push(`Couldn't create the alert: ${error.message}`, "error"),
  });
  if (!rules.isSuccess) return null;
  if (existing) {
    return (
      <Link
        href="/alerts?tab=rules"
        className="text-sm whitespace-nowrap text-tide-ink hover:underline"
        title={existing.description}
      >
        <span aria-hidden>✓</span> Alerts {existing.enabled ? "on" : "off"}
      </Link>
    );
  }
  return (
    <Button size="sm" disabled={create.isPending} onClick={() => create.mutate()}>
      Alert me on new matches
    </Button>
  );
}
