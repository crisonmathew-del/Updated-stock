"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { api, type AiSummaryState } from "@/lib/api";
import { formatDateTime } from "@/lib/format";

export const aiSummaryQuery = (symbol: string) =>
  ({ key: ["stock", symbol, "ai-summary"], path: `/api/stocks/${symbol}/ai-summary` }) as const;

/**
 * The optional AI summary (spec §6.12): written by Claude from this page's data on request,
 * labelled as such, cached for a day. Off (with a note saying how to switch it on) until the
 * server has an Anthropic key.
 */
export function AiSummaryPanel({ symbol }: { symbol: string }) {
  const q = aiSummaryQuery(symbol);
  const client = useQueryClient();
  const state = useQuery({ queryKey: q.key, queryFn: () => api.get<AiSummaryState>(q.path) });
  const write = useMutation({
    mutationFn: (refresh: boolean) =>
      api.post<AiSummaryState>(refresh ? `${q.path}?refresh=true` : q.path),
    onSuccess: (data) => client.setQueryData(q.key, data),
  });
  const data = state.data;
  if (!data) return null;
  if (!data.enabled) {
    return (
      <Section title="AI summary">
        <p className="text-sm text-muted">
          Off. Add an Anthropic API key (ANTHROPIC_API_KEY in .env) and restart the api service to
          get a short written thesis, catalyst and risks from this page&apos;s data.
        </p>
      </Section>
    );
  }
  const s = data.summary;
  return (
    <Section
      title="AI summary"
      note={s ? `${s.model} · ${formatDateTime(s.generated_at)}` : undefined}
    >
      {s ? (
        <div className="flex flex-col gap-2 text-sm">
          <p>{s.thesis}</p>
          <p>
            <span className="font-semibold">Catalyst. </span>
            {s.catalyst}
          </p>
          <div>
            <span className="font-semibold">Risks</span>
            <ul className="list-disc pl-5">
              {s.risks.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </div>
          {s.unverified.length > 0 && (
            <p role="note" className="text-xs text-warn">
              ◆ These numbers aren&apos;t in the data it was given: {s.unverified.join(", ")}. Check
              them against the panels before relying on them.
            </p>
          )}
          <p className="text-xs text-muted">
            Written by AI from the data on this page (the session of {s.as_of}); it can be wrong. No
            news source is connected yet, so it has no headlines.
          </p>
        </div>
      ) : (
        <p className="text-sm text-muted">
          A short thesis, catalyst and risks, written by {data.model} from this page&apos;s data.
        </p>
      )}
      <div className="flex items-center gap-3">
        <Button
          size="sm"
          variant={s ? "ghost" : "primary"}
          disabled={write.isPending}
          onClick={() => write.mutate(Boolean(s))}
        >
          {write.isPending ? "Writing…" : s ? "Write again" : "Summarise"}
        </Button>
        {write.error && (
          <span role="alert" className="text-sm text-fall">
            {write.error.message}
          </span>
        )}
      </div>
    </Section>
  );
}
