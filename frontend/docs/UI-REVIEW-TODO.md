# UI review: open items

Opened 2026-11-12 from a usability review of the web UI. It was driven with
Playwright against the mock-backed dev server at 1440, 820 and 390 px, in both
themes and keyboard-only, with the source read alongside. The direction it
measures against:

- The **chat stays close to Claude's UI**: warm palette, composer, sidebar and
  typography, message layout.
- **Modernize, don't revamp** everything else.

File and line references are from the review date and will drift.

Already done before the review, so not repeated here:
- The header was unified across the chat and the static sites, with a sun/moon
  theme toggle.
- The sidebar was restyled with 32px rows and hover-only row actions.
- The empty-state greeting was raised above centre.

Effort: S under half a day, M one to two days, L more.

---

## 1. Chat behaviour (highest user impact)

- [x] **The reply streams out of view after send** (M)
  - **Done 2026-11-13.**
  - **Problem:** The user's message is scrolled to the bottom edge, and
    streaming never auto-scrolls. On desktop only the mode chip and the jump
    button are visible while the answer builds below the fold. On a phone
    nothing of the reply is visible.
  - **Where:** `MessageList.tsx` ~102-136.
  - **Fix:** As in Claude: on send, scroll the user message to the top of the
    viewport and give the column a spacer of about the viewport height, so the
    reply streams into visible space without per-token scrolling. Keep the
    jump button.
- [x] **Message actions** (M)
  - **Decided 2026-11-13: do, including Edit and resend (needs backend support).**
  - **Done 2026-11-13: Copy and Report this reply; Retry on the latest reply and Edit on your own messages,** through `replace_from` on the backend (deployed to hugin and checked live), which drops the message and everything after it. Retry is on the latest reply only, because it deletes rather than branches.
  - **Problem:** A reply can't be copied, retried or regenerated, and a user
    message can't be edited and resent. Only code blocks have Copy, and Report
    covers the whole chat from the header.
  - **Where:** `MessageList.tsx` `MessageBubble` ~296-376.
  - **Fix:**
    - Under each assistant reply, a quiet icon row: Copy, Retry, and a
      per-reply "Report this reply". Always visible on the last reply, on hover
      or focus on older ones.
    - An edit pencil on user bubbles, shown on hover.
- [x] **Escape closes side panels** (S) *(done in the a11y/keyboard-focus merge)*
  - **Problem:** Escape doesn't close the Sources or Artifacts panel
    (`aria-pressed` stays true). Menus and the search modal already close.
  - **Fix:** Escape closes the topmost side panel and returns focus to its
    toggle.
- [x] **Themed delete confirmation** (S) *(done in the a11y/keyboard-focus merge)*
  - **Problem:** Deleting a chat or project uses the browser's `confirm()`,
    which is unthemed and gives no feedback afterwards.
  - **Where:** `Sidebar.tsx` ~428, ~474.
  - **Fix:** Use the existing dialog styling (as in `ReportDialog`), or delete
    at once with an Undo toast.

## 2. Closer to Claude

### Palette

- [x] **Warm token swap** (M)
  - **Done 2026-11-13, differently: the chat took the sites' ink and brass palette and Geist instead of a warm Claude palette.**
  - **Problem:** The palette is GitHub's (cool slate greys, blue `#58a6ff` /
    `#0a5cb8`).
  - **Where:** `src/index.css` 5-45, kept in step with
    `static/shared/style.css` (`themeSync.test.ts` enforces this). Components
    already use the tokens, so only the values change.
  - **Fix:** Starting point, to be checked against contrast:
    - Dark: bg `#1f1e1d / #262624 / #30302e`, text `#ece9e1`, secondary
      `#a8a49a`, border `#3a3936`.
    - Light: bg `#faf9f5 / #f3f1ea / #e9e6dc`, text `#2b2a26`, secondary
      `#6b685f`.
    - Accent: clay or terracotta, about `#c96442` light (darken towards
      `#b5573a` if white text on it misses 4.5:1) and `#d97757` dark.
    - Re-run `colorContrast.test.ts` and the e2e contrast suite afterwards.
- [ ] **Use the accent sparingly** (S)
  - **Decided 2026-11-13: deferred to the frontend redesign.**
  - **Fix:** Accent for send, links and the active item only. Focus and pressed
    states in neutral tones rather than accent fills everywhere.
- [ ] **Warm syntax theme for code blocks** (S, `lib/codeTheme`), and check
  `lib/chartPalette` against the new background.
  - **Decided 2026-11-13: deferred to the frontend redesign.**

### Composer

- [x] **Gentle focus** (S) *(done in the a11y/keyboard-focus merge)*
  - **Problem:** Any focus, including a mouse click, draws a 2px bright-blue
    outline with an offset around the whole card.
  - **Where:** `index.css` ~87-93.
  - **Fix:** A border tint plus a soft shadow. Keep the visible ring for
    keyboard focus only.
- [x] **16px input text** (S)
  - **Done 2026-11-13.**
  - **Problem:** The input is 14px while replies are 16px.
  - **Where:** `ChatInput.tsx` ~910.
  - **Fix:** 16px text and a slightly taller minimum height.
- [x] **The "Munin" badge does nothing** (S)
  - **Done 2026-11-13: a mode chip (Auto, Chat, Code, Research) that sends the slash command, which the router treats as absolute.**
  - **Problem:** The badge (`aria-hidden`) sits where Claude has its model
    picker but does nothing. In light mode its white circle disappears into the
    card.
  - **Where:** `ChatInput.tsx` ~1044-1058.
  - **Fix:** Replace it with something real (a mode or Deep Research chip, or
    an attachment count) or remove it.
- [x] **Clear send and stop buttons** (S)
  - **Done 2026-11-13.**
  - **Problem:** The send button looks disabled even when it's active, and the
    stop button is solid red (`ChatInput.tsx` ~1062-1066).
  - **Fix:** A 32-36px send button in the accent with a clearly enabled state.
    A neutral stop button: a dark square on a mid-grey circle.
- [x] **One column width** (S)
  - **Done 2026-11-13.**
  - **Problem:** The composer is `max-w-3xl` (768px) but the messages are
    `max-w-2xl` (672px), so their edges don't line up (`MessageList.tsx` ~168).
  - **Fix:** One shared width for both.
- [x] **Shorter placeholder** (S)
  - **Done 2026-11-13.**
  - **Problem:** The placeholder wraps to two lines on a phone.
  - **Fix:** "Ask Munin…", and teach `/` and `#` in a hint instead.

### Message layout

- [x] **User bubble** (S)
  - **Done 2026-11-13.**
  - **Problem:** It has a border and a "chat tail" corner (`rounded-br-sm`,
    `MessageList.tsx` ~305).
  - **Fix:** No border, a warm `bg-tertiary`, `rounded-2xl` on every corner.
- [ ] **Tool activity as one line** (M)
  - **Decided 2026-11-13: do.**
  - **Problem:** Tool calls show as a bordered monospace box (`TaskLog.tsx`
    ~238).
  - **Fix:** One sans line, collapsed by default ("Searched the web · 1 result
    ›").
- [x] **Drop the RESEARCH / CODE pill** (S)
  - **Done 2026-11-13: a quiet mode label in the reply's action row (and over a reply while it streams); a sent command shows as a label over the user's bubble.**
  - **Problem:** Every turn carries an uppercase routed-profile pill
    (`MessageList.tsx` ~72).
  - **Fix:** Drop it, or fold it into the tool line.
- [ ] **Tables with horizontal rules only** (S)
  - **Decided 2026-11-13: dropped.**
  - **Problem:** Tables are drawn as a full cell grid (`Markdown.tsx` ~238).
  - **Fix:** Horizontal rules only.
- [ ] **A reading face for replies** (M)
  - **Decided 2026-11-13: deferred to the frontend redesign.**
  - **Problem:** Everything is set in one sans at 16px / 1.7 (`index.css` ~63).
  - **Fix:** A serif for assistant prose only, e.g. Source Serif 4, Charter or
    Georgia at 16.5-17px / 1.65. Keep the UI in sans.

### Sidebar

- [x] **Pin moves into the ⋮ menu** (M) *(done in the a11y/keyboard-focus merge, with the roving tabindex)*
  - **Problem:** Every row has two tab stops (Pin and ⋮). It takes 31 Tabs to
    leave the sidebar. On a phone both icons are always visible and cut titles
    to about 18 characters.
  - **Fix:**
    - Move Pin into the ⋮ menu, as Claude does, leaving one row action.
    - A roving tabindex with arrow keys in the chat list, so the list is one
      Tab stop.
- [x] **14px menu items** (S) *(done in the a11y/keyboard-focus merge)*
  - **Problem:** Menu items are 12px (`MENU_ITEM` uses `text-xs`,
    `Sidebar.tsx` ~94).
  - **Fix:** 14px for menu items and the search modal.
- [ ] **A wider sidebar** (S)
  - **Decided 2026-11-13: do; pick the width by comparing side by side.**
  - **Problem:** The sidebar is 256px (`Sidebar.tsx` ~630, `w-64`).
  - **Fix:** About 272-288px.

## 3. Phone and tablet

- [ ] **Phone header like Claude's** (M)
  - **Decided 2026-11-13: do.**
  - **Problem:** On a phone the header is four or five unlabelled icons.
    Their titles never show on touch, and there is no New chat outside the
    drawer.
  - **Fix:** ☰, the chat title, and a New chat (pencil) icon. Temporary,
    Artifacts, Sources and Report move into a labelled "⋯" menu.
- [ ] **Site links at tablet width** (M)
  - **Decided 2026-11-13: do.**
  - **Problem:** At 820px with the sidebar open, the site nav disappears
    completely (`App.tsx`, `sidebarOpen ? 'lg:flex' : 'md:flex'`).
  - **Fix:** Below `lg`, open the sidebar as an overlay so the header keeps its
    links.
- [ ] **Site links on a phone** (S)
  - **Decided 2026-11-13: do.**
  - **Problem:** Knowledge, Search papers and Upload are reachable only through
    the account menu, which has no Home link.
  - **Fix:** Add Home to the account menu.
- [ ] **PWA install banner** (S)
  - **Decided 2026-11-13: do.**
  - **Problem:** It appears mid-conversation and pushes content.
  - **Fix:** Show it on the empty state only, or once after the first reply.
- [ ] **Temporary-chat banner** (S)
  - **Decided 2026-11-13: do.**
  - **Problem:** It spans the whole main area instead of the reading column.
- [ ] **Disclaimer spacing** (S)
  - **Decided 2026-11-13: do.**
  - **Fix:** 4-6px more space between the disclaimer and the composer on
    phones.

## 4. Empty state

- [ ] **Static mark in the greeting** (S)
  - **Decided 2026-11-13: dropped; the greeting keeps the feather spinner.**
  - **Problem:** The greeting uses the feather spinner, which is also the
    streaming indicator, so the page looks like it is still loading. The spinner
    belongs to the chat's working states only.
  - **Fix:** A static raven mark.
- [ ] **Starting points** (S, to decide)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Background:** The prompt-suggestion cards were tried and rejected on
    2026-11-12.
  - **Fix:** Something lighter that teaches the product, if anything. For
    example, a hint line about `/research`, `/code`, `/chat` and `#` sources.

## 5. Quick wins

- [ ] **Undiscoverable `#` sources** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** The placeholder only mentions `/` commands.
  - **Fix:** Add "Type # to pick sources" to the + menu or a hint line.
- [x] **Search-chats modal** (S) *(done in the a11y/keyboard-focus merge, with Ctrl/Cmd+K)*
  - **Problems:**
    - When a chat's preview equals its title, it prints twice
      (`Sidebar.tsx` ~1015-1019).
    - The input draws its own focus box inside the modal.
    - Nothing opens it from the keyboard.
  - **Fixes:** Hide the preview when it equals the title, drop the inner focus
    box, and bind Ctrl/Cmd+K.
- [ ] **Settings overlay** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problems:**
    - The chat toggles stay in the header on Settings, and Artifacts opens over
      the form.
    - There are two Save buttons on one page.
    - "Default persona" contradicts "Munin picks the mode".
  - **Fixes:** Hide the toggles on overlay views, keep one Save button, and
    pick one term.
- [ ] **Knowledge page copy** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** It shows "84 Papers (approx.)" next to "No papers mapped yet"
    (`EmbeddingMapView.tsx` ~184), plus "0 Research Groups / 0 Contributors"
    cards.
  - **Fix:** Reword to "The paper map hasn't been built yet. It updates
    nightly.", and hide cards whose count is zero.
- [ ] **Sources panel** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** "Research Groups (0)" expands to nothing.
  - **Fix:** Show "No groups yet", or disable the expander.
- [x] **Focus ring colour** (S) *(done in the a11y/keyboard-focus merge, one focus-ring token)*
  - **Problem:** Header items, the sidebar and the account button each use a
    different focus colour.
  - **Fix:** One focus colour everywhere.

## 6. Static sites (modernize, don't revamp)

- [x] **The homepage redesign**, in progress separately: mockups in the design *(done on design/site-redesign)*
  thread.
- [ ] **Search page on a phone** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** The topic input and the Search button share a row, leaving the
    input about 220px wide.
  - **Fix:** Stack them, or use an icon button.
- [ ] **Upload page** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** The Uppy dashboard keeps its own cyan "browse files" link and
    is narrower than the content column.
  - **Fix:** Theme the link to `--accent` and make the dashboard full width.
- [x] **Docs guide** (S) *(done on design/site-redesign, sticky contents list)*
  - **Problem:** It's a 500-line page with 9 sections and no table of contents.
  - **Fix:** A sticky "On this page" table of contents from 1024px up.
- [x] **Footers** (S) *(done on design/site-redesign, shared site footer)*
  - **Problem:** The `|`-separated link rows in the search, research and upload
    footers.
  - **Fix:** One muted line.
- [ ] **Phone nav** (S)
  - **Open: to compare side by side as artifacts before deciding.**
  - **Problem:** The static nav scrolls sideways with "Upload" cut off and
    nothing to show there is more.
  - **Fix:** A fade or shadow at the edge of the scrolling nav.

## Keep

- **Accessibility basics:**
  - The skip link to the message box.
  - Real `role=menu` menus with arrow keys, Escape and focus return.
  - `aria-pressed` on the toggles.
  - Descriptive `aria-label`s on every icon button.
- **Contrast:** sidebar secondary and tertiary text pass AA in both themes.
- **The unified header**, and the theme choice shared by every site through the
  cookie.
- **Narrow screens:** no sideways scroll at 390px. Wide tables and code scroll
  inside their own box.
- **Composer menus:** the `/` command menu with descriptions, the `#` sources
  picker with counts, and the + menu with a described Deep Research toggle.
- **Temporary chat:** clearly signalled by the warning-tinted toggle and the
  banner with "Turn off".
- **Honest copy:** the Report dialog and the artifacts empty state.
- **Small touches:** the jump-to-bottom button, and the language label and
  Copy button on code blocks.
