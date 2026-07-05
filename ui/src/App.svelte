<script lang="ts">
  import AppShell from "./components/AppShell.svelte";
  import InvestigationList from "./components/InvestigationList.svelte";
  import InvestigationView from "./components/InvestigationView.svelte";
  import NewInvestigation from "./components/NewInvestigation.svelte";
  import Settings from "./components/Settings.svelte";
  import KnowledgeBase from "./components/KnowledgeBase.svelte";
  import Monitor from "./components/Monitor.svelte";
  import ClaimVerify from "./components/ClaimVerify.svelte";
  import Placeholder from "./components/Placeholder.svelte";
  import { currentRoute } from "./lib/router.svelte";

  let route = $derived(currentRoute.value);
</script>

<AppShell>
  {#if route.name === "dashboard"}
    <InvestigationList />
  {:else if route.name === "investigation"}
    <InvestigationView id={route.params.id} tab={route.params.tab ?? "overview"} />
  {:else if route.name === "domains"}
    <Placeholder
      title="Domains"
      body="Browse + create + edit relevance hypothesis presets. Stub for now -- backend GET /api/domains is already live (see http://127.0.0.1:5050/api/domains). The wizard at #/new lets you pick a domain when creating an investigation."
    />
  {:else if route.name === "new"}
    <NewInvestigation />
  {:else if route.name === "settings"}
    <Settings />
  {:else if route.name === "knowledge"}
    <KnowledgeBase />
  {:else if route.name === "monitor"}
    <Monitor />
  {:else if route.name === "claim"}
    <ClaimVerify />
  {:else}
    <Placeholder title="Not found" body="Unknown route." />
  {/if}
</AppShell>
