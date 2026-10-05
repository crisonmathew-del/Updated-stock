import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";
import { plex } from "./fonts";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: "Breakout",
  description: "Growth-stock scanning, scoring and alerts",
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#12161d" },
    { media: "(prefers-color-scheme: light)", color: "#f7f5f0" },
  ],
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);
  return (
    <html lang="en" className={`${theme} ${plex.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
