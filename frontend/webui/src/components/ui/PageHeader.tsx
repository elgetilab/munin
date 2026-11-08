// The header of a full-page view (Settings, Project settings, Admin): the
// page's title, and one way back in the same place on every page.

export function PageHeader({ title, onBack, className = '' }: { title: string; onBack: () => void; className?: string }) {
  return (
    <div className={`flex items-center justify-between gap-3 ${className}`}>
      <h2 className="text-xl font-semibold text-text-primary">{title}</h2>
      <button
        type="button"
        onClick={onBack}
        className="text-text-secondary hover:text-text-primary transition-colors cursor-pointer text-sm"
      >
        Back to chat
      </button>
    </div>
  );
}
