/**
 * An error shown in the page, announced to screen readers as it appears.
 *
 *   <ErrorNotice message="Could not load users." onRetry={reload} />
 *
 * - message: plain text saying what went wrong.
 * - onRetry: shows a Retry button.
 * - onDismiss: shows a dismiss button.
 * - className: for spacing around the notice, such as margins.
 */

export interface ErrorNoticeProps {
  message: string;
  onRetry?: () => void;
  onDismiss?: () => void;
  className?: string;
}

export function ErrorNotice({ message, onRetry, onDismiss, className = '' }: ErrorNoticeProps) {
  return (
    <div
      role="alert"
      className={`flex items-start gap-3 px-4 py-3 bg-error/10 border border-error rounded-lg text-error text-sm ${className}`}
    >
      <span className="flex-1 min-w-0 wrap-anywhere">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex-shrink-0 px-2 py-0.5 border border-error rounded text-xs font-medium hover:bg-error/10 cursor-pointer"
        >
          Retry
        </button>
      )}
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="flex-shrink-0 px-1 hover:opacity-70 cursor-pointer"
        >
          <span aria-hidden="true">&#10005;</span>
        </button>
      )}
    </div>
  );
}
