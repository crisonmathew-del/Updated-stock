"use client";

import { useQuery } from "@tanstack/react-query";
import { Panel } from "@/components/admin/panel";
import { api, type Groups } from "@/lib/api";
import { formatChange, formatRankChange } from "@/lib/format";

export function GroupsPanel() {
  const { data, error } = useQuery({
    queryKey: ["groups"],
    queryFn: () => api.get<Groups>("/api/groups?limit=20"),
    refetchInterval: 60_000,
  });

  return (
    <Panel
      title="Leadership"
      description="Top industry groups (median RS + 3- and 6-month returns) and the sector ETFs ranked by RS."
    >
      {error && <p className="text-sm text-fail">{error.message}</p>}
      {data && (
        <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
          <div className="overflow-x-auto">
            <h3 className="mb-2 text-sm font-medium">
              Industry groups {data.date && `· ${data.date}`}
            </h3>
            {data.groups.length === 0 ? (
              <p className="text-sm text-muted">
                No ranked groups yet. Groups need industry codes (SEC_USER_AGENT) and 3+ stocks.
              </p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-3 font-normal">#</th>
                    <th className="py-2 pr-3 font-normal">4 wk</th>
                    <th className="py-2 pr-3 font-normal">Group</th>
                    <th className="py-2 pr-3 font-normal">Stocks</th>
                    <th className="py-2 pr-3 font-normal">Median RS</th>
                    <th className="py-2 pr-3 font-normal">3 mo</th>
                    <th className="py-2 font-normal">Leaders</th>
                  </tr>
                </thead>
                <tbody className="tabular divide-y divide-border">
                  {data.groups.map((g) => (
                    <tr key={g.group_id}>
                      <td className="py-2 pr-3 font-medium">{g.rank}</td>
                      <td className="py-2 pr-3 whitespace-nowrap text-muted">
                        {formatRankChange(g.rank_change_4w)}
                      </td>
                      <td className="py-2 pr-3">
                        {g.name}
                        <span className="block text-xs text-muted">{g.sector}</span>
                      </td>
                      <td className="py-2 pr-3">{g.members}</td>
                      <td className="py-2 pr-3">{g.median_rs ?? "—"}</td>
                      <td className="py-2 pr-3">{formatChange(g.return_3m)}</td>
                      <td className="py-2">{g.tt_passing}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          <div className="overflow-x-auto">
            <h3 className="mb-2 text-sm font-medium">Sectors</h3>
            {data.sectors.length === 0 ? (
              <p className="text-sm text-muted">No sector ETF history yet.</p>
            ) : (
              <table className="w-full text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-2 pr-3 font-normal">#</th>
                    <th className="py-2 pr-3 font-normal">4 wk</th>
                    <th className="py-2 pr-3 font-normal">Sector</th>
                    <th className="py-2 font-normal">3 mo</th>
                  </tr>
                </thead>
                <tbody className="tabular divide-y divide-border">
                  {data.sectors.map((s) => (
                    <tr key={s.symbol}>
                      <td className="py-2 pr-3 font-medium">{s.rank}</td>
                      <td className="py-2 pr-3 whitespace-nowrap text-muted">
                        {formatRankChange(s.rank_change_4w)}
                      </td>
                      <td className="py-2 pr-3">
                        {s.sector} <span className="text-muted">{s.symbol}</span>
                      </td>
                      <td className="py-2">{formatChange(s.return_3m)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
