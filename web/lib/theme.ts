/** Theme preference: dark by default, remembered in a cookie so the server renders it. */
export type Theme = "dark" | "light";

export const THEME_COOKIE = "breakout_theme";

export function parseTheme(value: string | undefined | null): Theme {
  return value === "light" ? "light" : "dark";
}

/** Switch the page and remember the choice for a year. */
export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.remove("dark", "light");
  root.classList.add(theme);
  document.cookie = `${THEME_COOKIE}=${theme}; path=/; max-age=31536000; samesite=lax`;
  window.dispatchEvent(new CustomEvent<Theme>("breakout:theme", { detail: theme }));
}

export function currentTheme(): Theme {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

/** A CSS token's value (e.g. "--rise") for canvas drawing (charts). */
export function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Subscribe to theme changes (for canvas charts and server-rendered images). */
export function subscribeTheme(onChange: () => void): () => void {
  window.addEventListener("breakout:theme", onChange);
  return () => window.removeEventListener("breakout:theme", onChange);
}
