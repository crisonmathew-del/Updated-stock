import { AppHeader } from "@/components/app-header";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <AppHeader />
      {children}
      <footer className="mx-auto mt-auto w-full max-w-5xl px-6 py-6 text-xs text-muted">
        Screening signals, not financial advice.
      </footer>
    </>
  );
}
