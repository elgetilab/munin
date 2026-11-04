import { useEffect, useState } from 'react';

// Three-slide presenter deck at /demo. It is unlinked on purpose: the presenter
// opens the URL, and the live cards on slide 3 hand over to a real chat. Prompts
// live in /demo/demo.json so they can change without a rebuild.

export interface DemoCard {
  id: string;
  title: string;
  blurb: string;
  prompt: string;
  kind: 'chat' | 'research';
  useProject?: boolean;
  budgets?: { max_subq?: number; screen_keep?: number; read_cap?: number };
}

interface DemoConfig {
  projectId: string | null;
  savedResearchChat: string | null;
  cards: DemoCard[];
}

interface Props {
  slide: number;
  onSlide: (slide: number) => void;
  offline: boolean;
  onRun: (card: DemoCard, projectId: string | null) => void;
  onOpenChat: (conversationId: string) => void;
}

const SLIDE_COUNT = 3;

export function DemoDeck({ slide, onSlide, offline, onRun, onOpenChat }: Props) {
  const [config, setConfig] = useState<DemoConfig | null>(null);
  const [configError, setConfigError] = useState(false);

  useEffect(() => {
    // The service worker serves static files cache-first; the query defeats that.
    fetch(`/demo/demo.json?v=${Date.now()}`)
      .then(r => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then(setConfig)
      .catch(() => setConfigError(true));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight' && slide < SLIDE_COUNT - 1) onSlide(slide + 1);
      if (e.key === 'ArrowLeft' && slide > 0) onSlide(slide - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [slide, onSlide]);

  return (
    <div data-testid="demo-deck" className="fixed inset-0 z-50 bg-bg-primary text-text-primary flex flex-col">
      <main className="flex-1 min-h-0 overflow-y-auto px-8 md:px-16 py-10 flex flex-col justify-center">
        <div className="max-w-6xl w-full mx-auto">
          {slide === 0 && <SlideWhat />}
          {slide === 1 && <SlideWhy />}
          {slide === 2 && (
            <SlideLive
              config={config}
              configError={configError}
              offline={offline}
              onRun={onRun}
              onOpenChat={onOpenChat}
            />
          )}
        </div>
      </main>

      <footer className="flex items-center justify-center gap-4 pb-6">
        <button
          onClick={() => onSlide(slide - 1)}
          disabled={slide === 0}
          className="px-3 py-1.5 text-sm rounded-lg border border-border text-text-secondary hover:text-text-primary disabled:opacity-30 cursor-pointer disabled:cursor-default"
        >
          Back
        </button>
        <span className="text-sm text-text-secondary tabular-nums">{slide + 1} / {SLIDE_COUNT}</span>
        <button
          data-testid="demo-next"
          onClick={() => onSlide(slide + 1)}
          disabled={slide === SLIDE_COUNT - 1}
          className="px-3 py-1.5 text-sm rounded-lg border border-border text-text-secondary hover:text-text-primary disabled:opacity-30 cursor-pointer disabled:cursor-default"
        >
          Next
        </button>
      </footer>
    </div>
  );
}

function SlideWhat() {
  return (
    <div className="grid md:grid-cols-2 gap-10 items-center">
      <div>
        <p className="text-accent text-sm font-medium mb-3">Munin</p>
        <h1 className="text-4xl font-semibold leading-tight mb-5">
          A research assistant built around our consortium's literature
        </h1>
        <p className="text-lg text-text-secondary mb-6">
          Munin answers from your papers and the wider literature, shows its work
          as it goes, and says so when it can't find something.
        </p>
        <ul className="space-y-3 text-base">
          <li><strong>Your papers.</strong> <span className="text-text-secondary">More than 8,000 PDFs uploaded by consortium members, in a collection of about 68,000.</span></li>
          <li><strong>The right mode for each question.</strong> <span className="text-text-secondary">Chat, code or research, chosen per turn.</span></li>
          <li><strong>Open and self-hosted.</strong> <span className="text-text-secondary">Open source, and the model is swappable.</span></li>
        </ul>
      </div>
      <figure>
        <img
          src="/demo/cluster.webp"
          alt="The open case of the Munin cluster machine, with two GeForce RTX graphics cards"
          className="w-full rounded-xl border border-border"
        />
        <figcaption className="text-sm text-text-secondary mt-3">
          The machine Munin normally runs on. It's offline tonight, so this demo
          runs on our fallback server with a hosted model.
        </figcaption>
      </figure>
    </div>
  );
}

function Stat({ value, label, detail }: { value: string; label: string; detail: string }) {
  return (
    <div className="rounded-xl border border-border bg-bg-secondary p-6">
      <div className="text-5xl font-semibold tabular-nums mb-3">{value}</div>
      <div className="text-base font-medium mb-2">{label}</div>
      <div className="text-sm text-text-secondary">{detail}</div>
    </div>
  );
}

function SlideWhy() {
  return (
    <div>
      <h1 className="text-4xl font-semibold leading-tight mb-3">Answers you can check</h1>
      <p className="text-lg text-text-secondary mb-8">
        The accuracy comes from how Munin works through a question, reading sources before it answers.
      </p>
      <div className="grid md:grid-cols-3 gap-5 mb-6">
        <Stat
          value="87%"
          label="correct on a literature benchmark"
          detail="Against 39% for the same model alone and 21% for plain document retrieval."
        />
        <Stat
          value="100/100"
          label="fabricated papers refused"
          detail="No invented citations to papers in the collection."
        />
        <Stat
          value="~2.5 min"
          label="for a literature question"
          detail="About 7 tool calls each. Real reading takes a moment, and you can watch it happen."
        />
      </div>
      <p className="text-sm text-text-secondary">
        LitQA2, 199 questions; Qwen3.8-27B on the cluster with the full collection. MuninAI paper, in
        preparation. Tonight's live demo uses a different model and a small document set, and citations
        are always worth checking.
      </p>
    </div>
  );
}

function SlideLive({ config, configError, offline, onRun, onOpenChat }: {
  config: DemoConfig | null;
  configError: boolean;
  offline: boolean;
  onRun: (card: DemoCard, projectId: string | null) => void;
  onOpenChat: (conversationId: string) => void;
}) {
  return (
    <div>
      <h1 className="text-4xl font-semibold leading-tight mb-3">Try it live</h1>
      <p className="text-lg text-text-secondary mb-8">
        Each card opens a real chat, answered live. Use the Slides button to come back.
      </p>
      {offline && <p className="text-sm text-error mb-4">The model is offline, so the live cards are disabled.</p>}
      {configError && <p className="text-sm text-error mb-4">The demo settings could not be loaded.</p>}
      <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-4">
        {config?.cards.map(card => (
          <div key={card.id} className="rounded-xl border border-border bg-bg-secondary p-5 flex flex-col">
            <h2 className="text-lg font-medium mb-2">{card.title}</h2>
            <p className="text-sm text-text-secondary flex-1 mb-4">{card.blurb}</p>
            <div className="flex flex-wrap items-center gap-3">
              <button
                data-testid={`demo-run-${card.id}`}
                onClick={() => onRun(card, config.projectId)}
                disabled={offline || (card.useProject === true && !config.projectId)}
                className="px-3 py-1.5 text-sm rounded-lg bg-accent text-bg-primary font-medium hover:bg-accent-hover disabled:opacity-40 cursor-pointer disabled:cursor-default"
              >
                Run live
              </button>
              {card.kind === 'research' && config.savedResearchChat && (
                <button
                  data-testid="demo-saved-report"
                  onClick={() => onOpenChat(config.savedResearchChat as string)}
                  className="text-sm text-accent hover:underline cursor-pointer"
                >
                  Saved report
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
