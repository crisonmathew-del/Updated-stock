import { TopBar } from "@/components/shell/top-bar";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <TopBar />
      {children}
      <footer className="mx-auto mt-auto w-full max-w-[1600px] px-4 py-6 text-xs text-muted">
        Screening signals, not financial advice. The platform never places trades.
      </footer>
    </>
  );
}
