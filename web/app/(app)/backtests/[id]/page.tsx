import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BacktestReportView } from "@/components/backtests/report";

export const metadata: Metadata = { title: "Backtest · Breakout" };

export default async function BacktestPage({ params }: PageProps<"/backtests/[id]">) {
  const { id } = await params;
  const run = Number(id);
  if (!Number.isInteger(run) || run < 1) notFound();
  return <BacktestReportView id={run} />;
}
