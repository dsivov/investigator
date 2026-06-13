// Shared Cytoscape loader. Registers the fcose layout extension exactly
// once for the whole app -- calling cytoscape.use(fcose) a second time
// (e.g. when the user opens the Graph tab and then the TMFG tab) throws,
// which previously left the second canvas blank.
//
// Lazy: the cytoscape + fcose chunks are only fetched the first time a
// network tab mounts.

import type cytoscapeType from "cytoscape";

let cyPromise: Promise<typeof cytoscapeType> | null = null;

export function loadCytoscape(): Promise<typeof cytoscapeType> {
  if (!cyPromise) {
    cyPromise = Promise.all([import("cytoscape"), import("cytoscape-fcose")]).then(
      ([cy, fcose]) => {
        cy.default.use(fcose.default);
        return cy.default;
      }
    );
  }
  return cyPromise;
}
