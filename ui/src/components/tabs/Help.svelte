<script lang="ts">
  // Static help / glossary tab. No data fetching — a friendly orientation for
  // newcomers to the terms and tabs.
</script>

<div class="flex-1 overflow-y-auto scrollbar p-6">
  <div class="max-w-3xl space-y-8 text-sm leading-relaxed text-slate-300">

    <section>
      <h2 class="text-lg font-semibold text-slate-100 mb-2">What this tool does</h2>
      <p>
        It reads a body of news articles (and any documents you add) about a
        subject or several stories, then builds a <strong>knowledge graph</strong>:
        the people and organisations involved, the events, and the relationships
        between them — each traced back to the source it came from. The goal is to
        surface the connections an analyst would otherwise find only by reading
        hundreds of articles by hand.
      </p>
    </section>

    <section>
      <h2 class="text-lg font-semibold text-slate-100 mb-3">Key terms</h2>
      <dl class="space-y-3">
        <div>
          <dt class="text-slate-100 font-medium">Thread</dt>
          <dd class="text-slate-400">
            One search angle / query in an investigation. A single-query
            investigation has one thread; a cross-story investigation runs several
            (e.g. three related stories) so the system can find what connects them.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Actor (entity)</dt>
          <dd class="text-slate-400">
            A named person, organisation, or place the system extracted from the
            articles.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Event</dt>
          <dd class="text-slate-400">
            A concrete incident — who did what to whom, when and where (e.g. "Arrest
            of X over bribery charges"). Events carry participant roles like
            <em>victim</em>, <em>perpetrator</em>, <em>witness</em>.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Relationship</dt>
          <dd class="text-slate-400">
            An attested link between two actors (affiliation, ownership, employment,
            etc.), carrying the quote and source URL it came from. Three articles
            asserting the same link become one relationship of weight three.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Evidence</dt>
          <dd class="text-slate-400">
            The specific article passages that mention an actor and either support
            or contradict the investigation's hypothesis. Evidence is what gives an
            actor its <em>confidence</em>.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Theme</dt>
          <dd class="text-slate-400">
            A tight group of four actors that keep co-occurring — the system's answer
            to "which actors belong together?". Themes are ranked by an
            evidence-weighted score, so the top theme is the best-corroborated
            structure, not just the densest.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Bridge</dt>
          <dd class="text-slate-400">
            An actor that appears in more than one thread — the structural backbone
            of any cross-story claim. (Single-query investigations have no bridges.)
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Confidence vs. Score</dt>
          <dd class="text-slate-400">
            <strong>Confidence</strong> is how well-attested an actor is on its own.
            <strong>Score</strong> = confidence × relevance-to-the-subject, so it
            ranks by how central an actor is to <em>this</em> investigation. On a
            broad query, sort by score to keep the subject's own network on top and
            push topically-similar-but-unrelated names down.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Domain &amp; hypothesis</dt>
          <dd class="text-slate-400">
            The domain (e.g. <em>sanctions evasion</em>, <em>criminal
            investigation</em>) sets the <em>hypothesis</em> — the test each piece of
            evidence is scored against. It decides what counts as relevant.
          </dd>
        </div>
      </dl>
    </section>

    <section>
      <h2 class="text-lg font-semibold text-slate-100 mb-3">Reading each tab</h2>
      <dl class="space-y-3">
        <div>
          <dt class="text-slate-100 font-medium">Overview</dt>
          <dd class="text-slate-400">
            The headline read: top actors (or cross-thread bridges, for multi-query
            runs), top themes, and corpus coverage.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Graph</dt>
          <dd class="text-slate-400">
            The full network. Click any actor to see its attested role, sources, and
            relationships. Colours mark which thread an actor came from.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">TMFG themes</dt>
          <dd class="text-slate-400">
            A filtered, triangulated view that groups the graph into four-actor
            themes (shaded polygons). <strong>Solid</strong> edges are corroborated
            by a source; <strong>dashed</strong> edges are structural
            <em>hypotheses</em> the algorithm suggests — leads to verify, not facts.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Data</dt>
          <dd class="text-slate-400">
            The graph as sortable tables — Actors, Events, Relationships, Evidence.
            Actors default to sorting by <em>score</em> (most relevant first); click
            any column to re-sort, or use the search box to filter.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Report</dt>
          <dd class="text-slate-400">
            An analyst-style write-up generated from the graph, with confidence
            language and source citations.
          </dd>
        </div>
        <div>
          <dt class="text-slate-100 font-medium">Sources</dt>
          <dd class="text-slate-400">
            The articles and documents the investigation drew on, with links.
          </dd>
        </div>
      </dl>
    </section>

    <section class="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <h2 class="text-base font-semibold text-slate-100 mb-2">How to read it confidently</h2>
      <ul class="list-disc pl-5 space-y-1.5 text-slate-400">
        <li>
          A link means the system found the connection <em>attested in a source</em>,
          not that it is proven — open the evidence and read the article.
        </li>
        <li>
          It surfaces <strong>co-occurrence and source-claimed relationships</strong>,
          not causation. "X bridges A and B" means the same X appears in both — not
          that X caused B.
        </li>
        <li>
          The graph is only as good as the corpus. A lead is a place to start
          reading; the URLs are there because a human still has to verify.
        </li>
      </ul>
    </section>

  </div>
</div>
