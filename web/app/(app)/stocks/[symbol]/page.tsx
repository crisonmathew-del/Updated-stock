import type { Metadata } from "next";
import { StockPage } from "@/components/stock/stock-page";

export async function generateMetadata({
  params,
}: PageProps<"/stocks/[symbol]">): Promise<Metadata> {
  const { symbol } = await params;
  return { title: `${decodeURIComponent(symbol).toUpperCase()} · Breakout` };
}

export default async function Page({ params }: PageProps<"/stocks/[symbol]">) {
  const { symbol } = await params;
  return <StockPage symbol={decodeURIComponent(symbol).toUpperCase()} />;
}
