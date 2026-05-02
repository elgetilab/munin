import { useState, useRef, useEffect } from 'react';
import type { Persona } from '../lib/types';

interface PersonaSelectorProps {
  personas: Persona[];
  selected: string;
  onSelect: (id: string) => void;
}

function formatName(name: string) {
  // "Meitner - Chat" → "Meitner [Chat]"
  const match = name.match(/^(.+?)\s*[-–]\s*(.+)$/);
  if (match) {
    return <>{match[1]} <span className="text-text-secondary font-normal">[{match[2]}]</span></>;
  }
  return name;
}

export function PersonaSelector({ personas, selected, onSelect }: PersonaSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const current = personas.find(p => p.id === selected);

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-2 px-2 py-1.5 rounded-lg text-sm text-text-secondary hover:bg-bg-tertiary hover:text-text-primary transition-colors cursor-pointer"
      >
        {current && (
          <img src={current.icon_url} alt="" className="w-5 h-5 rounded-full" />
        )}
        <span className="font-medium">{current ? formatName(current.name) : 'Select persona'}</span>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="opacity-50">
          <polyline points={open ? '18 15 12 9 6 15' : '6 9 12 15 18 9'} />
        </svg>
      </button>

      {open && (
        <div className="absolute bottom-full right-0 mb-2 w-64 bg-bg-secondary border border-border rounded-xl shadow-lg z-50 overflow-hidden">
          {personas.map(p => (
            <button
              key={p.id}
              onClick={() => { onSelect(p.id); setOpen(false); }}
              className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-bg-tertiary transition-colors cursor-pointer ${
                p.id === selected ? 'bg-bg-tertiary' : ''
              }`}
            >
              <img src={p.icon_url} alt="" className="w-6 h-6 rounded-full flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-text-primary">{formatName(p.name)}</div>
                <div className="text-[11px] text-text-secondary truncate">{p.description}</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
