import { useState, useEffect } from 'react';
import { fetchStatus } from '../lib/api';
import type { SystemStatus } from '../lib/types';

export function useStatus(pollInterval = 60000) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const failCountRef = { current: 0 };

  useEffect(() => {
    let mounted = true;

    async function check() {
      try {
        const s = await fetchStatus();
        if (mounted) {
          failCountRef.current = 0;
          setStatus(s);
        }
      } catch {
        // Only mark offline after 3 consecutive failures.
        // A single 429 or transient error shouldn't trigger sleeping page.
        failCountRef.current += 1;
        if (mounted && failCountRef.current >= 3) {
          setStatus({
            vllm: { status: 'offline', model: '' },
            services: {},
            timestamp: new Date().toISOString(),
          } as SystemStatus);
        }
      }
    }

    check();
    const id = setInterval(check, pollInterval);
    return () => { mounted = false; clearInterval(id); };
  }, [pollInterval]);

  return status;
}
