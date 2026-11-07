// Class strings for the few places where dark and light need different treatment
// beyond swapping a colour token. Dark keeps its original classes exactly; the
// light override is a [data-theme=light] ancestor variant, which outranks the
// plain utility by specificity, so a theme switch applies without a reload.

/** The logo files are a black raven. --logo-filter turns it white in dark only. */
export const LOGO_FILTER = '[filter:var(--logo-filter)]';

/** Dims the page behind a mobile drawer. */
export const SCRIM = 'bg-black/50 [[data-theme=light]_&]:bg-scrim';

/**
 * Backdrop behind a dialog. Dark blurs the page under a bg-primary veil; in light
 * that veil would be white and wash the dialog out, so light dims with the scrim.
 */
export const DIALOG_BACKDROP = 'bg-bg-primary/70 backdrop-blur-sm [[data-theme=light]_&]:bg-scrim';

/**
 * Table row hover on a bg-secondary card. bg-primary/30 darkens it slightly in
 * dark, but in light bg-primary is white and the change would not show.
 */
export const ROW_HOVER = 'hover:bg-bg-primary/30 [[data-theme=light]_&]:hover:bg-bg-tertiary/60';
