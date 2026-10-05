"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "@/lib/api";
import { safeNext } from "@/lib/format";

export function LoginForm() {
  const router = useRouter();
  const next = safeNext(useSearchParams().get("next"));
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      await api.post("/api/auth/login", {
        email: String(form.get("email") ?? ""),
        password: String(form.get("password") ?? ""),
      });
      router.replace(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Try again.");
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
      <label className="flex flex-col gap-1.5 text-sm">
        Email
        <input
          name="email"
          type="email"
          autoComplete="username"
          required
          className="rounded-md border border-border bg-background px-3 py-2 focus-visible:outline-foreground"
        />
      </label>
      <label className="flex flex-col gap-1.5 text-sm">
        Password
        <input
          name="password"
          type="password"
          autoComplete="current-password"
          required
          className="rounded-md border border-border bg-background px-3 py-2 focus-visible:outline-foreground"
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-fail">
          {error}
        </p>
      )}
      <button
        type="submit"
        disabled={pending}
        className="rounded-md bg-foreground px-3 py-2 text-sm font-medium text-background disabled:opacity-60"
      >
        {pending ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
