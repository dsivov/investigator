// A tiny cross-component signal. Bumping `dataVersion.value` lets any
// component that reads it (e.g. the sidebar's recent list) re-fetch after a
// mutation such as a delete or a new investigation launch.

export const dataVersion = $state({ value: 0 });

export function refreshInvestigations() {
  dataVersion.value++;
}
