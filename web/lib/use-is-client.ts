import { useSyncExternalStore } from "react";

const subscribe = () => () => {};

/** False while rendering on the server and hydrating, true after: for text that depends on the
 * viewer's time zone or locale, which the server can't know. */
export function useIsClient(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
