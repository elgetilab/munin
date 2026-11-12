import type { SystemStatus } from '../lib/types';
import { degradedMessage } from '../lib/degraded';

/** Shows lib/degraded.ts's notice above the chat, or nothing. */
export function DegradedBanner({ status }: { status: SystemStatus | null | undefined }) {
  const message = degradedMessage(status);
  if (!message) return null;
  return (
    <div
      role="status"
      data-testid="degraded-banner"
      className="mx-4 mt-3 px-4 py-3 rounded-lg text-sm bg-warning/10 border border-warning text-warning"
    >
      {message}
    </div>
  );
}
