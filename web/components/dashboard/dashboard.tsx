"use client";

import { LeadingGroups, SectorRotation } from "./leadership";
import { Breakouts, NearPivot, SignalFeed, TopSetups } from "./lists";
import { MarketPanel } from "./market-panel";

/**
 * The home page (spec §8.2), in three stacked columns so panels of different lengths leave no
 * gaps: the market (regime with its reasons, breadth) and sector rotation; top setups, names
 * about to break out and recent breakouts with their status; leading groups and the signal
 * feed. Every row opens the stock page, and `[` / `]` there flip through the list it came from.
 */
export function Dashboard() {
  return (
    <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 items-start gap-4 px-4 py-4 md:grid-cols-2 xl:grid-cols-3">
      <h1 className="sr-only">Dashboard</h1>
      <div className="flex flex-col gap-4">
        <MarketPanel />
        <SectorRotation />
      </div>
      <div className="flex flex-col gap-4">
        <TopSetups />
        <NearPivot />
        <Breakouts />
      </div>
      <div className="flex flex-col gap-4 md:col-span-2 xl:col-span-1">
        <LeadingGroups />
        <SignalFeed />
      </div>
    </main>
  );
}
