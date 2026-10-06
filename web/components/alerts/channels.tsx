"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { deliveryLabel } from "@/components/alerts/history";
import { ALERTS, STATUS } from "@/components/alerts/queries";
import { Section } from "@/components/ui/section";
import { Button } from "@/components/ui/button";
import { api, type Alert, type AlertsStatus } from "@/lib/api";
import { formatAgo } from "@/lib/format";
import { useToasts } from "@/stores/toast";

const FIELD = "h-8 rounded-md border border-border bg-background px-2 text-sm";
const KEYS = [
  "alerts_email_enabled",
  "alert_email_immediate_priority",
  "quiet_hours_start",
  "quiet_hours_end",
  "daily_digest_enabled",
  "daily_digest_time",
  "weekly_digest_enabled",
  "alert_min_grade",
  "alert_cooldown_minutes",
] as const;
type Key = (typeof KEYS)[number];
type Values = Record<Key, string | number | boolean>;

const STREAMER_TEXT: Record<string, string> = {
  streaming: "Streaming",
  idle: "Idle (no live feed configured)",
  "replay finished": "Replay finished",
  error: "Stopped by an error",
  down: "Not running",
  unknown: "Starting",
};

function Status() {
  const status = useQuery({
    queryKey: STATUS.key,
    queryFn: () => api.get<AlertsStatus>(STATUS.path),
    refetchInterval: 30_000,
  });
  const s = status.data;
  if (!s) return null;
  const feed = s.streamer.provider === "alpaca" ? "Alpaca" : s.streamer.provider;
  return (
    <Section title="Delivery">
      <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
        <dt className="text-muted">In-app</dt>
        <dd>
          <span aria-hidden className="text-rise">
            ✓
          </span>{" "}
          On: the bell, toasts and the alerts centre.
        </dd>
        <dt className="text-muted">Email</dt>
        <dd>
          <span aria-hidden className={s.email.configured ? "text-rise" : "text-fall"}>
            {s.email.configured ? "✓" : "✕"}
          </span>{" "}
          {s.email.configured ? s.email.detail : `Not set up: ${s.email.detail}`}
        </dd>
        <dt className="text-muted">Live feed</dt>
        <dd>
          <span
            aria-hidden
            className={
              s.streamer.state === "streaming"
                ? "text-rise"
                : s.streamer.state === "error" || s.streamer.state === "down"
                  ? "text-fall"
                  : "text-muted"
            }
          >
            {s.streamer.state === "streaming"
              ? "✓"
              : s.streamer.state === "error" || s.streamer.state === "down"
                ? "✕"
                : "–"}
          </span>{" "}
          {STREAMER_TEXT[s.streamer.state] ?? s.streamer.state}
          {feed && feed !== "none" ? ` · ${feed}` : ""}
          {s.streamer.since ? ` · since ${formatAgo(s.streamer.since)}` : ""}
          {s.streamer.detail && (
            <span className="block text-xs text-muted">{s.streamer.detail}</span>
          )}
        </dd>
        {s.quiet_hours_now && (
          <>
            <dt className="text-muted">Now</dt>
            <dd>Quiet hours: emails are held for the next digest.</dd>
          </>
        )}
      </dl>
    </Section>
  );
}

function TestButtons() {
  const client = useQueryClient();
  const [result, setResult] = useState<string | null>(null);
  const test = useMutation({
    mutationFn: (channel: "in_app" | "email") => api.post<Alert>("/api/alerts/test", { channel }),
    onSuccess: (alert, channel) => {
      void client.invalidateQueries({ queryKey: [...ALERTS, "list"] });
      const look = deliveryLabel(channel, alert.delivery[channel]);
      const detail = alert.delivery[channel] ?? "";
      setResult(`${look.mark} ${look.text}${detail.includes(":") ? ` (${detail})` : ""}.`);
    },
    onError: (error) => setResult(`✕ ${error.message}`),
  });
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" disabled={test.isPending} onClick={() => test.mutate("in_app")}>
        Send a test in-app alert
      </Button>
      <Button size="sm" disabled={test.isPending} onClick={() => test.mutate("email")}>
        Send a test email
      </Button>
      {result && (
        <p role="status" className="text-sm">
          {result}
        </p>
      )}
    </div>
  );
}

/** Channel settings (email, quiet hours, digests, which setups alert), delivery status and test
 * sends. */
export function AlertChannels() {
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: () => api.get<{ items: { key: string; value: unknown }[] }>("/api/settings"),
  });
  const found = Object.fromEntries((settings.data?.items ?? []).map((i) => [i.key, i.value]));
  const initial = Object.fromEntries(
    KEYS.map((k) => [k, found[k] as string | number | boolean]),
  ) as Values;
  return (
    <div className="flex flex-col gap-4">
      <Status />
      {settings.data && <ChannelSettings initial={initial} />}
    </div>
  );
}

function ChannelSettings({ initial }: { initial: Values }) {
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [values, setValues] = useState<Values>(initial);
  const save = useMutation({
    mutationFn: (changes: Partial<Values>) => api.patch("/api/settings", { changes }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["settings"] });
      void client.invalidateQueries({ queryKey: STATUS.key });
      push("Alert settings saved.");
    },
  });
  const set = (change: Partial<Values>) => setValues({ ...values, ...change });
  const quiet = Boolean(values.quiet_hours_start);

  return (
    <>
      <form
        aria-label="Alert settings"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(values);
        }}
        className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-4 text-sm"
      >
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 font-semibold">Email</legend>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={Boolean(values.alerts_email_enabled)}
              onChange={(e) => set({ alerts_email_enabled: e.target.checked })}
            />
            Send alerts by email
          </label>
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-muted">Email straight away</span>
            <select
              value={String(values.alert_email_immediate_priority)}
              onChange={(e) => set({ alert_email_immediate_priority: e.target.value })}
              className={FIELD}
            >
              <option value="high">High-priority alerts only (the rest go in the digest)</option>
              <option value="normal">Every alert</option>
            </select>
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={quiet}
                onChange={(e) =>
                  set(
                    e.target.checked
                      ? { quiet_hours_start: "22:00", quiet_hours_end: "07:00" }
                      : { quiet_hours_start: "", quiet_hours_end: "" },
                  )
                }
              />
              Quiet hours
            </label>
            {quiet && (
              <>
                <label className="flex items-center gap-1">
                  <span className="sr-only">Quiet from</span>
                  <input
                    type="time"
                    value={String(values.quiet_hours_start)}
                    onChange={(e) => set({ quiet_hours_start: e.target.value })}
                    className={FIELD}
                  />
                </label>
                <span className="text-muted">to</span>
                <label className="flex items-center gap-1">
                  <span className="sr-only">Quiet until</span>
                  <input
                    type="time"
                    value={String(values.quiet_hours_end)}
                    onChange={(e) => set({ quiet_hours_end: e.target.value })}
                    className={FIELD}
                  />
                </label>
                <span className="text-xs text-muted">US/Eastern; held emails go in the digest</span>
              </>
            )}
          </div>
        </fieldset>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 font-semibold">Digests</legend>
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={Boolean(values.daily_digest_enabled)}
                onChange={(e) => set({ daily_digest_enabled: e.target.checked })}
              />
              Daily digest on trading days at
            </label>
            <label>
              <span className="sr-only">Daily digest time</span>
              <input
                type="time"
                value={String(values.daily_digest_time)}
                onChange={(e) => set({ daily_digest_time: e.target.value })}
                className={FIELD}
              />
            </label>
            <span className="text-xs text-muted">ET</span>
          </div>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={Boolean(values.weekly_digest_enabled)}
              onChange={(e) => set({ weekly_digest_enabled: e.target.checked })}
            />
            Weekly review on Sunday at 18:00 ET
          </label>
        </fieldset>
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 font-semibold">What alerts you</legend>
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-muted">
              Setup alerts on stocks you don&apos;t hold or watch: grade
            </span>
            <select
              value={String(values.alert_min_grade)}
              onChange={(e) => set({ alert_min_grade: e.target.value })}
              className={FIELD}
            >
              {["A+", "A", "B", "C"].map((g) => (
                <option key={g} value={g}>
                  {g === "A+" ? "A+ only" : `${g} or better`}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-wrap items-center gap-2">
            <span className="text-muted">Repeat the same alert at most every</span>
            <input
              type="number"
              min={0}
              value={Number(values.alert_cooldown_minutes)}
              onChange={(e) => set({ alert_cooldown_minutes: Number(e.target.value) })}
              className={`${FIELD} tabular w-20`}
            />
            <span className="text-muted">minutes (390 = once a session)</span>
          </label>
        </fieldset>
        <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save settings
          </Button>
          {save.isError && (
            <p role="alert" className="text-fall">
              {save.error.message}
            </p>
          )}
        </div>
      </form>
      <Section title="Test">
        <p className="text-sm text-muted">
          Sends one alert now on that channel (quiet hours don&apos;t apply to tests).
        </p>
        <TestButtons />
      </Section>
    </>
  );
}
