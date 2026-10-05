export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="rounded border border-border bg-surface-2 px-1.5 py-px font-sans text-[11px] text-muted">
      {children}
    </kbd>
  );
}
