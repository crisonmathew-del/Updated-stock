/** The settings query, shared by the settings page, the guide (client) and the guide's server
 * prefetch, so they read and fill the same cache entry. */
export const SETTINGS_QUERY = { key: ["settings"], path: "/api/settings" } as const;
