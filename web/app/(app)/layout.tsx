import { TopBar } from "@/components/shell/top-bar";

export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <TopBar />
      {children}
      {/* pb on phones keeps the footer clear of the bottom tab bar. */}
      <footer className="mx-auto mt-auto w-full max-w-[1600px] px-4 pt-6 pb-20 text-xs text-muted sm:pb-6">
        Screening signals, not financial advice. The platform never places trades.
      </footer>
    </>
  );
}
