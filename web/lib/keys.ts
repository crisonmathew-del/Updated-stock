/** True when a key press is meant for a text field, so global shortcuts should ignore it. */
export function isTyping(event: KeyboardEvent): boolean {
  const target = event.target as HTMLElement | null;
  if (!target) return false;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable;
}

/** A plain key press: no Ctrl/Cmd/Alt (Shift allowed for symbols like "[" on some layouts). */
export function plainKey(event: KeyboardEvent): boolean {
  return !event.metaKey && !event.ctrlKey && !event.altKey;
}
