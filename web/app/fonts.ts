import localFont from "next/font/local";

/** IBM Plex Sans (SIL Open Font License, see fonts/IBM-Plex-Sans-LICENSE.txt), self-hosted. */
export const plex = localFont({
  src: [
    { path: "./fonts/ibm-plex-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "./fonts/ibm-plex-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
  ],
  variable: "--font-plex",
  display: "swap",
});
