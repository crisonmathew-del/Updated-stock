import type { Metadata } from "next";
import { Suspense } from "react";
import { LoginForm } from "@/components/login-form";

export const metadata: Metadata = { title: "Sign in · Breakout" };

export default function LoginPage() {
  return (
    <main className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center gap-6 px-6 py-16">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Breakout</h1>
        <p className="text-sm text-muted">Sign in to continue.</p>
      </header>
      <Suspense>
        <LoginForm />
      </Suspense>
      <p className="text-xs text-muted">
        No account yet? Create one with <code>make create-user email=you@example.com</code>.
      </p>
    </main>
  );
}
