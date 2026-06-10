import type { Persona } from '../lib/types';

/**
 * Horizontal divider marking where the conversation switched persona
 * (via delegate_to_persona or a manual persona change). Driven by the
 * per-message `persona` field, so it renders identically live and after
 * a transcript reload. Distinct from CompactBoundaryDivider (summary)
 * by its accent colour and the persona icon.
 */
export function PersonaDivider({
  personaId,
  personas,
  reason,
}: {
  personaId: string;
  personas?: Persona[];
  reason?: string | null;
}) {
  const persona = personas?.find(p => p.id === personaId);
  // Persona names often include a dash like "Curie - Research" — use the
  // short label, matching personaName() in MessageList.
  const name = persona ? (persona.name.split('-')[0].trim() || persona.name) : personaId;

  return (
    <div className="my-3 select-none" role="separator" aria-label={`Switched to ${name}`}>
      <div className="w-full flex items-center gap-2 text-[11px] uppercase tracking-wider text-accent/80">
        <span className="h-px flex-1 bg-accent/25" />
        <span className="flex items-center gap-1.5 px-2">
          {persona?.icon_url ? (
            <img src={persona.icon_url} alt="" className="w-3.5 h-3.5 rounded-full opacity-80" />
          ) : (
            <span aria-hidden>{'↳'}</span>
          )}
          <span>now {name}</span>
        </span>
        <span className="h-px flex-1 bg-accent/25" />
      </div>
      {reason ? (
        <div className="mt-1 text-center text-[11px] text-text-tertiary normal-case tracking-normal">
          {reason}
        </div>
      ) : null}
    </div>
  );
}
