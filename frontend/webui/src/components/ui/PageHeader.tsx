// The header of a full-page view (Settings, Project settings, Admin): the
// page's title in a lit band like the Knowledge page's and the sites' page
// heads, and one way back in the same place on every page. `width` is the
// page's own column, so the title lines up with the content under it.

export function PageHeader({ title, onBack, width = 'max-w-xl' }: { title: string; onBack: () => void; width?: string }) {
  return (
    <div className="page-head border-b border-border">
      <div className={`${width} mx-auto px-6 pt-8 pb-6 md:pt-10 md:pb-7 flex items-end justify-between gap-3`}>
        <h2 className="text-[28px] md:text-[34px] leading-tight font-semibold tracking-[-0.03em] text-text-primary">{title}</h2>
        <button
          type="button"
          onClick={onBack}
          className="mb-1.5 text-text-secondary hover:text-text-primary transition-colors cursor-pointer text-sm"
        >
          Back to chat
        </button>
      </div>
    </div>
  );
}
