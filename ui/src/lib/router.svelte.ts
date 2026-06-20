// Minimal hash-based router using Svelte 5 runes.
// Routes:
//   #/                    -> Dashboard (Investigation list)
//   #/investigations/:id  -> Investigation view (six tabs)
//   #/investigations/:id/:tab -> Investigation view, tab pre-selected
//   #/domains             -> Domain library (placeholder)
//   #/new                 -> New Investigation wizard (placeholder)

export interface Route {
  name: "dashboard" | "investigation" | "domains" | "new" | "settings" | "not_found";
  params: Record<string, string>;
}

function parseHash(hash: string): Route {
  const h = (hash || "#/").replace(/^#/, "");
  const parts = h.replace(/^\/+/, "").split("/").filter(Boolean);

  if (parts.length === 0) return { name: "dashboard", params: {} };
  if (parts[0] === "domains") return { name: "domains", params: {} };
  if (parts[0] === "settings") return { name: "settings", params: {} };
  if (parts[0] === "new") return { name: "new", params: {} };
  if (parts[0] === "investigations" && parts[1]) {
    return {
      name: "investigation",
      params: { id: parts[1], tab: parts[2] || "overview" },
    };
  }
  return { name: "not_found", params: {} };
}

export const currentRoute = $state<{ value: Route }>({ value: parseHash(location.hash) });

window.addEventListener("hashchange", () => {
  currentRoute.value = parseHash(location.hash);
});

export function navigate(path: string) {
  if (!path.startsWith("#")) path = "#" + path;
  if (location.hash !== path) {
    location.hash = path;
  } else {
    // Same hash -- force a route refresh (e.g. tab change inside investigation)
    currentRoute.value = parseHash(location.hash);
  }
}

export function investigationUrl(id: string, tab: string = "overview"): string {
  return `#/investigations/${id}/${tab}`;
}
