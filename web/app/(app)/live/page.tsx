import type { Metadata } from "next";
import { LiveBoard } from "@/components/live/live-board";

export const metadata: Metadata = { title: "Live · Breakout" };

export default function LivePage() {
  return <LiveBoard />;
}
