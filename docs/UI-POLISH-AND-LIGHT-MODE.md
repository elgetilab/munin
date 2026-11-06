# UI polish list and light mode plan

Follow-up work from the frontend review of 2026-11-05. The review's bugs and usability
findings were fixed and merged to `main` on 2026-11-06. This holds what is still to do.

## Polish items

The 32 findings rated polish, all left for later. The fixes may have changed some of
them in passing, so check each against the current code first. One checker confirmed
them, so treat them as reviewed, not proven. File and line references are from
`787e3d2`, before those fixes, so they will have moved. Effort is S, M or L. The
screenshots named were local to the review and are not in the repo.

### #30 Phone drawer has no Escape or focus handling
- Where: `frontend/webui/src/App.tsx:682` (Phone drawer, effort M)
- Seen: The drawer (682-698) closes only by tapping the backdrop, a div with no label, or by choosing an item (sidebar__phone_drawer__phone.png). Escape does not close it, focus does not move in, and Tab can reach the page behind it. There is no visible close control inside the drawer. The search overlay (721-726) has the same focus gaps: no role=dialog, no focus trap and no focus return, plus an unlabelled input.
- Change: Give the drawer and the search overlay role='dialog', aria-modal, focus on open and focus return on close. Add Escape to the drawer and a close button in its top row. Rename the search overlay's 'Esc' button to 'Close' on phones, where there is no Esc key (sidebar__search_recent__phone.png).

### #31 Generating dot stays on after a background turn ends
- Where: `frontend/webui/src/components/Sidebar.tsx:858` (Chat row generating indicator, effort S)
- Seen: The dot (sidebar__expanded__desktop.png) comes from the chat list, which refetches only on reloadKey or refreshKey (lines 99-108). Nothing polls, and App bumps refreshKey only for the open chat (App.tsx 315-322). A background chat keeps pulsing until some other action refreshes the list. The dot's only label is a title, which is not on touch.
- Change: While any listed chat is generating, poll the list every 10 to 15 seconds and stop when none are. Add a visually hidden 'Generating' label.

### #32 Long titles are cut early with no way to read them
- Where: `frontend/webui/src/components/Sidebar.tsx:877` (Chat and project rows, effort S)
- Seen: sidebar__expanded__desktop.png shows 'Markdown showcase: NM...' cut at about 170px of a 240px row, because the invisible star and kebab keep their space. The title link (875-878) and project name (line 406) have no title attribute, so the full text is unavailable without opening the chat.
- Change: Add title={chat.title} and title={proj.name}. Optionally let the title use the full width and overlay the controls with a gradient on hover or focus.

### #33 Move menu is confusing: it offers the current project, and its arrow points to a flyout that doesn't exist
- Where: `frontend/webui/src/components/Sidebar.tsx:965` (Move to project menu, effort S)
- Seen: sidebar__chat_row_move_menu__desktop.png shows a bare project list with no heading or way back. The right arrow at line 934 suggests a flyout, but the list replaces the parent menu in the same spot. For a filed chat the list includes its current project, because lines 965-973 don't filter. 'Remove from project' appears in both menus (937 and 957). 'Unfiled' in the delete confirmation (line 247) is a term the UI shows nowhere else.
- Change: Add a 'Move to' heading with a back button, or a real flyout. Filter out the current project and show one Remove item. Reword the confirmation to 'Chats in this project will stay in your chat list.'

### #34 Empty project gives no next step
- Where: `frontend/webui/src/components/Sidebar.tsx:519` (Expanded empty project, effort S)
- Seen: sidebar__project_expanded_empty__desktop.png shows only 'No chats' in 11px. The way to start one is hidden in the kebab, and nothing explains what a project does for someone new to Munin. The new project control (line 544) is also 11px and less prominent than the chats around it.
- Change: Replace it with a 'New chat in this project' link using newChatPath(proj.id), and one line saying projects keep chats together and can share instructions.

### #35 Three different ways to close a full-page view
- Where: `frontend/webui/src/components/Settings.tsx:247` (Settings, Admin, Project settings close controls, effort S)
- Seen: Settings says 'Back to chat' (Settings.tsx 247), Admin says '✕ Close' (AdminPanel.tsx 89-92), and Project settings has an unlabelled chevron on the left plus Cancel (ProjectSettings.tsx 64 and 159, project_settings__populated__phone.png).
- Change: Use one shared page header with a labelled 'Back to chat' control in the same position on all three.

### #55 Toast timers overwrite each other, and the toast is not announced
- Where: `frontend/webui/src/App.tsx:1002` (Toast component, effort S)
- Seen: From code. Each caller sets its own setTimeout(() => setToast(null)) (App.tsx:434, 455, 995) with no cancel, so an older timer can clear a newer toast early. The container has no role='status' and the close button has no name. The toast is fixed at bottom-6, where the composer sits once a chat has messages, so it will cover the composer and Send; the only capture is on the empty screen where it clears the composer: shots/toast__demo_card_failed__phone.png
- Change: Add showToast(message, ms) to uiStore that owns a single timer. Add role='status' and aria-label='Dismiss'. Position the toast at the top of the content area, or above the composer.

### #59 Action buttons differ across sibling cards
- Where: `frontend/webui/src/components/MessageList.tsx:340` (PlanCard, MemoryProposalPill, ClarificationCard, Continue, ResearchTimeline, effort M)
- Seen: Primary actions use different size, radius and text: PlanCard and memory px-2 py-1 rounded at 12px with text-white (PlanCard.tsx:232, MemoryProposalPill.tsx:59); Clarification px-4 py-1.5 rounded-lg with text-bg-primary (ClarificationCard.tsx:110); Continue is a neutral rounded-full pill at text-sm with no cursor-pointer (MessageList.tsx:340); Cancel and View the full report are text links; revoke is a lowercase underlined link (PlanCard.tsx:276). Compare shots/messages__memory_proposal_pill__phone.png with shots/messages__paused_at_budget__phone.png The plan and memory buttons are about 26px tall on a phone, which is small for a touch target.
- Change: Add one small shared Button (primary, secondary, quiet) for these cards with a minimum 36px touch height on coarse pointers. Make Continue primary, since it is the one action available at that point, and revoke a sentence-case quiet button.

### #60 Emoji and SVG icons are mixed
- Where: `frontend/webui/src/components/PlanCard.tsx:27` (PlanCard, slash menu, upload banner, research activity, effort S)
- Seen: PlanCard uses emoji status icons, and the cancelled mark is narrower so its row misaligns: shots/messages__plan_card__desktop.png Slash menu emoji (ChatInput.tsx:12-14, shot shots/composer__slash_menu__desktop.png), paperclip in the upload banner (ChatInput.tsx:560), search and page emoji in research activity (ResearchTimeline.tsx:152). TaskLog and the attach menu use line SVGs, and TaskLog's header says no emojis. Emoji also vary by platform.
- Change: Reuse the TaskLog icon set: export it to a shared module and replace the emoji with SVGs, with plain check, spinner, circle and cross marks for plan status. Keep the aria-label on plan items.

### #61 Stripped links open the app in a new tab
- Where: `frontend/webui/src/components/Markdown.tsx:220` (Markdown links, effort S)
- Seen: From code. safeUrlTransform returns an empty string for a disallowed scheme (line 194), described as a no-op anchor at line 176. The a renderer still outputs href='' with target='_blank', so clicking opens the current app URL in a new tab rather than doing nothing. The security behaviour itself is correct.
- Change: When href is empty, render a span with the link text instead of an anchor, and check Markdown.test.tsx for assertions that need updating.

### #62 Copy says 'Copied' even when the copy failed
- Where: `frontend/webui/src/components/Markdown.tsx:119` (Markdown code block Copy, effort S)
- Seen: From code. writeText is not awaited or caught, and setCopied(true) runs regardless. If the permission is denied the user is told it worked and pastes old clipboard contents; if navigator.clipboard is unavailable the handler throws. Control in shots/messages__reply_markdown_code__desktop.png
- Change: Make the handler async: await writeText in try/catch, show 'Copied' on success and 'Copy failed' on failure.

### #64 Drop can exceed the image limit, and + does not close the knowledge picker
- Where: `frontend/webui/src/components/ChatInput.tsx:278` (Composer attachments and attach button, effort S)
- Seen: From code. addImage checks pendingImages.length from the render closure, and handleDroppedFiles awaits it in a loop (line 497), so the length never updates within one drop: dropping five images adds all five past MAX_IMAGES = 3. Separately, the + onClick toggles only attachMenuOpen (line 809), and outside-click handling ignores it because the button is inside attachRef (line 218). With the picker open, + opens the menu underneath it at the same position (lines 820, 892), so it appears to do nothing.
- Change: Use the setPendingImages updater form to enforce the cap, setting the error when it is exceeded. In the + handler: if the picker is open, close it; otherwise toggle the menu.

### #65 Summary divider text cannot be selected, and its name does not match its state
- Where: `frontend/webui/src/components/CompactBoundaryDivider.tsx:13` (CompactBoundaryDivider, effort S)
- Seen: select-none on the wrapper also covers the opened summary (line 13), so it cannot be copied, though it exists to show what the model sees. aria-label='Show conversation summary' overrides the visible count and stays the same when open (line 19). There is no chevron, unlike TaskLog rows: shots/messages__compact_boundary_closed__phone.png A count of one would read '1 messages' (line 23).
- Change: Move select-none to the button only. Remove aria-label and let aria-expanded plus the visible text describe it. Add the same chevron as TaskLog. Handle the singular.

### #66 Task log raw JSON is hard for scientists to use, and DOIs are not links
- Where: `frontend/webui/src/components/TaskLog.tsx:343` (TaskLog expanded rows and RAG row, effort M)
- Seen: DOIs are plain text (line 343), and the research note type carries ref.doi but ResearchTimeline.tsx:173 shows only the title. Expanded rows show raw JSON, so paper titles sit inside braces: shots/messages__task_log_expanded__desktop.png The Arguments box has no height limit while Result has max-h-48 (lines 231, 236), so a long run_python argument takes over the log on a phone, with its code shown as an escaped JSON string.
- Change: Link DOIs to https://doi.org/ in both components. For results with papers or results arrays, render a short linked title list and keep the JSON behind 'Show raw'. Show run_python code as a code block. Add max-h-48 overflow-y-auto to Arguments.

### #89 Close, back, save and field styles differ across sibling pages and dialogs
- Where: `frontend/webui/src/components/ProjectSettings.tsx:77` (Settings, ProjectSettings, AdminPanel, dialogs, effort M)
- Seen: Exit controls: 'Back to chat' (Settings:247), a bare chevron plus Cancel (ProjectSettings:64,158), '✕ Close' (AdminPanel:92). Save labels: 'Save changes' and 'Saved' (Settings), 'Save profile' (Settings), 'Save' and 'Saved!' (ProjectSettings:156). Labels: sentence case text-sm in Settings, uppercase xs in ProjectSettings:77 and admin Field. Widths: max-w-lg, max-w-xl, max-w-4xl. Add dialogs say Cancel, edit dialogs say Close, and buttons use rounded in admin, rounded-lg elsewhere. Backdrops differ between Report (blurred) and admin (bg-black/60). UsersTab and GroupsTab each have their own Field, Input and modal. Compare settings__profile_section__desktop.png and project_settings__populated__desktop.png under shots/.
- Change: Pick one pattern for each and share it through small components: a page header with one back control, Field, Input, Button and Dialog. Suggested: 'Back to chat' on pages, 'Cancel' for unsaved-work exits, 'Save' and 'Saved' everywhere.

### #90 Settings wording assumes knowledge of system prompts and personas
- Where: `frontend/webui/src/components/Settings.tsx:343` (Settings > Munin Profile, ProjectSettings > Instructions and Default Persona, effort S)
- Seen: 'This is injected into every conversation' (Settings:343) and 'injected into the system prompt' (ProjectSettings:102) are developer language. 'Default persona' has no explanation anywhere on the page (Settings:377, ProjectSettings:117). The empty option reads 'None (use system default)' in Settings and 'Use global default' in projects, for what looks like linked behaviour. The account block has no heading while later sections do, so 'Munin Profile' looks like the same thing as the profile above it. Shot: shots/settings__munin_profile_section__desktop.png.
- Change: Use 'Munin reads this at the start of every chat'. Add one helper line saying what a persona is. Align the empty options after confirming precedence, for example 'Use my default from Settings'. Add an 'Account' heading above the first block and rename 'Munin Profile' to 'How Munin works with you'.

### #92 Avatar picker: limit is unexplained, errors use alert(), re-picking the same file does nothing
- Where: `frontend/webui/src/components/Settings.tsx:137` (Settings > Profile picture, effort S)
- Seen: The 150 KB limit appears only in a blocking alert() after choosing a file (line 138), which is small for phone photos. The input value is never reset, so Remove followed by picking the same file fires no change event. Changing or removing the picture only applies after 'Save changes', further down the section, with no hint. Shot: shots/settings__profile_section__desktop.png.
- Change: Show 'JPG or PNG, up to 150 KB' under Upload photo and inline error text instead of alert(). Reset e.target.value after reading. Better: downscale in a canvas before upload so phone photos work. Add 'Unsaved' or a similar cue after a change.

### #93 Announcement controls offer an action that does nothing and a bare 'Failed' message
- Where: `frontend/webui/src/components/Settings.tsx:646` (Settings > Admin: Announcement, effort M)
- Seen: With an empty box and no announcement the main button is 'Clear announcement', fully enabled: shots/settings__quick_start_section__desktop.png. A failure shows only 'Failed' (line 639), in secondary text colour rather than error. Level names are lowercase, unlike all other buttons. The section sits at the bottom of personal Settings although the Admin Dashboard exists for admin tasks. Admins must scroll past their API keys to reach it.
- Change: Disable the button when the box is empty and nothing is stored. Use 'Could not save the announcement' in error colour. Capitalise levels. Move the section to an Announcement tab or card in AdminPanel, and offer a preview of the banner.

### #96 Admin summary cards go stale on some tabs, and Activity repeats rows with raw values
- Where: `frontend/webui/src/components/AdminPanel.tsx:97` (AdminPanel > summary cards and Activity tab, effort M)
- Seen: Refresh runs only on Activity and Usage (line 44), so 'Online now' freezes while the admin is on Metrics, Users or Groups. On phones the cards take about 180px above every tab, pushing tables and charts down: shots/admin__users__phone.png. Activity lists the same people under Online or Recent and again under All tracked users: admin__activity__desktop.png. The source shows raw 'api_key' in low-contrast 10px text. The card says 'Active today' and the section says 'Recent'. Usage shows Period as raw '2026-11'.
- Change: Show the cards only on Activity and Usage. Show online and recent as a status filter on a single list or remove the duplicates. Map source to 'Browser' and 'API key'. Use one term for Recent and Active today after checking the backend definition. Format the period as 'November 2026'.

### #97 Hard-coded '100M tokens/month' sits above a live limit and could contradict it
- Where: `frontend/webui/src/components/Settings.tsx:437` (Settings > API Keys, effort S)
- Seen: The text at line 437 is fixed while the card below reads usage.current_month.tokens_limit. With mock data they disagree on screen, 100M above 1.0M: shots/settings__api_keys_section__desktop.png. The real figure is config-driven per quotas.yml.example, whose comment says it counts API-key usage and browser chat is free. The page does not say so. Quick start also has no Copy button, and its messages line is cut off until scrolled sideways on phones: settings__quick_start_section__phone.png.
- Change: Render the limit from usage, hiding it while unloaded, and label the card 'Monthly API usage (browser chat is not counted)' after confirming this with the gateway. Add a Copy button to Quick start with the same success handling as the key.

### #123 Inline previews give no loading text, caption or sign that they open
- Where: `frontend/webui/src/components/InlineArtifacts.tsx:131` (InlineArtifacts preview and loading, effort S)
- Seen: Lines 98 and 131 render an empty h-32 box while loading, with no text or aria-busy. messages__inline_artifacts__phone.png: image previews have no filename or caption, whereas cards above and below do. The 'Open <title>' hint exists only as a title tooltip, so touch users get no cue that tapping opens the panel.
- Change: Give the placeholder 'Loading preview...' with aria-busy. Add a slim caption under the image with the filename, and an 'Open' affordance, matching ArtifactCard's text styles.

### #124 Protein viewer lacks a colour legend and clips the structure in the tall phone panel
- Where: `frontend/webui/src/components/ProteinViewer.tsx:158` (ProteinViewer, effort S)
- Seen: protein_viewer__style_controls__desktop.png and the rendered_inline shots: Spectrum runs N to C terminus and the Confidence bands are pLDDT cut-offs (widgets/proteinRuntime.js), and highlighted residues appear magenta, but nothing on screen says so. 'RCSB' and 'AlphaFold' links carry no sign that they open an external site. artifact_panel__widget_protein__phone.png: both ends of the helix are cut off in h-[60vh], while the same structure fits inline at h-80 (protein_viewer__rendered_inline__phone.png). The mock is an unusually long helix, so check with a real structure.
- Change: Add a one-line legend that follows the colour choice, such as 'Blue N-terminus to red C-terminus', the four pLDDT bands, and 'Magenta: highlighted residues'. Label the link 'Open in RCSB' with an external-link icon. On phones, limit the panel height to around 100vw so the fit is not governed by a tall narrow box.

### #125 Papers tile changes value depending on when the map loads
- Where: `frontend/webui/src/components/KnowledgePage.tsx:59` (KnowledgePage stats, effort S)
- Seen: totalPapers falls back to the sum of topic counts until the map responds or if it fails. With the same mock catalog, knowledge_page__overview__desktop.png shows 450 and embedding_map__error__desktop.png shows 2,840. The size of the gap is a product of mock data, and the sum is only right if each paper has exactly one topic, which I did not verify. Separately, the tile says 16 Topics while the map says '6 topics'.
- Change: Show a placeholder dash until the map stats arrive, and on failure either omit the tile or label it approximate. Rename the map line to 'Clusters (6)' so it does not compete with the Topics count.

### #126 Toolbar 'PDF' does not say it means print to PDF, and wording varies between sibling controls
- Where: `frontend/webui/src/components/ArtifactPanel.tsx:280` (ArtifactPanel toolbar; KnowledgePage; EmbeddingMapView, effort S)
- Seen: artifact_panel__markdown__phone.png shows 'PDF' beside Download; the code comment calls it 'Save as PDF', and it opens a print dialog. On the Knowledge page 'PDF' instead means download a file (KnowledgePage.tsx:423). Other wording: 'Show fewer' versus 'Show less' (KnowledgePage.tsx:198, 291); 'Available Knowledge' versus 'Knowledge Base' for the same catalog, with the pill and nav both called 'Knowledge' but doing different things; 'Jane Doe (Elgeti Lab (Leipzig))' with nested parentheses (line 432, knowledge_page__browse_group__desktop.png); and 'Paper Embedding Map... projected into 2D space' with no statement that nearby dots mean similar papers.
- Change: Label the button 'Save as PDF'. Use 'Show less' everywhere and 'Jane Doe · Elgeti Lab (Leipzig)'. Rename the panel 'Search scope' or 'Limit search to', so it differs from the Knowledge page. Rewrite the caption: 'Paper map: each dot is a paper, and nearby dots cover similar topics. Colours show topic groups. Drag to pan, scroll to zoom, click a dot to open its topic.'

### #127 Browse list is full-width while the overview is capped at 4xl
- Where: `frontend/webui/src/components/KnowledgePage.tsx:343` (KnowledgePage browse view, effort S)
- Seen: knowledge_page__browse_group__desktop.png: rows span about 1390px with the title at the far left and the PDF button at the far right, whereas the overview (line 125) uses `max-w-4xl mx-auto`.
- Change: Add `max-w-4xl mx-auto w-full` to the browse wrapper so the page width stays the same when moving between views.

### #139 Support widget controls are small on phones, and the floating button covers footer text
- Where: `frontend/static/shared/support-widget.js:202` (Support widget on phone, effort S)
- Seen: The phone rule only moves the sheet and shrinks the floating button to 40px. The close button is a 14px glyph with 4px padding (lines 81-88), the footer buttons are 12px text with 6px padding (lines 158-167), and fields are 13px (line 121), which makes iOS Safari zoom on focus. See support_widget__open_signed_in__phone.png. In upload__denied_not_leader__phone.png the ? button covers the end of 'A project by the'.
- Change: Inside the 480px rule, make the close and footer buttons at least 44px tall and fields 16px, and keep the floating button at 44px. Add bottom padding to footer on phones so the ? button clears the text.

### #149 Docs pages lose their top spacing because of a CSS override
- Where: `frontend/static/docs/index.html:13` (Docs index, guide and architecture, effort S)
- Seen: `main { padding: 64px 0 }` is beaten by `.container { padding: 0 24px }` from style.css on `<main class="container">`, as .container has higher specificity. docs_index__top__desktop.png has the heading almost touching the header at about y=78, against y=125 on upload__full_page__desktop.png. guide.html and architecture.html repeat the pattern with 48px.
- Change: Use `main.container { padding-top: 48px; padding-bottom: 48px; }` on all three docs pages, so the side gutter is kept.

### #150 Sibling pages differ on support access, footer links and back navigation
- Where: `frontend/static/docs/architecture.html:454` (Static pages shared chrome, effort S)
- Seen: support-widget.js is loaded on landing, search, upload, docs index, guide and chat, but not on architecture, research or static maintenance, where the footer also has no Support link. guide.html has '← Back to Docs' (line 129) but architecture.html has none. Footers give 'Get Access' and 'Support' as two links to the same mailto, and none link to Docs.
- Change: Use one footer everywhere: Search, Chat, Upload, Docs, Support, with 'Get Access' dropped or pointed at a page that explains access. Add the widget and back link to the pages missing them. Keep the header duplication out of scope unless pages move to a shared include.

### #151 Search phone layout and small details need tidying
- Where: `frontend/static/search/index.html:318` (Paper Search layout, effort S)
- Seen: The Year 'From' placeholder is clipped to 'Fror' on desktop by the inline 70px width (search__top__desktop.png). On phones the stats bar squeezes into two columns of three-line labels (search__with_stats__phone.png), and the results header does the same with its two parts (search__results__phone.png). Results use coloured emoji icons, including a confused face for no results (search__no_results__desktop.png), unlike the line icons used in the widget and chat.
- Change: Set the year inputs to about 90px; add flex-wrap to .stats-bar and stack .results-header below 480px; replace the emoji with the same line icons or plain text labels.

### #152 Search never stops its loading animation
- Where: `frontend/static/search/index.html:471` (Paper Search, loading state, effort S)
- Seen: The stop functions returned by createVortex and createRotatingMessage are discarded (lines 471-472). The results replace the canvas, but feather-vortex.js:136 keeps scheduling frames and has no isConnected check, so each search adds another hidden animation loop and message timer for the life of the page.
- Change: Keep both stop functions and call them in the finally block of search().

### #153 Demo slides hard-code 'tonight' on a URL that stays live
- Where: `frontend/webui/src/components/DemoDeck.tsx:120` (DemoDeck, slides 1 and 2, effort S)
- Seen: The caption says 'It's offline tonight, so this demo runs on our fallback server with a hosted model', and line 164 says 'Tonight's live demo uses a different model and a small document set'. demo.json can change only the cards, so changing these lines needs a rebuild, and the statements become false after that event (demo_deck__slide_1_what__desktop.png).
- Change: Move these two sentences into optional demo.json fields and show them only when set, so the default deck makes no claim about today's setup.

### #154 Static and in-app maintenance pages format the start time differently
- Where: `frontend/static/maintenance/index.html:147` (Static maintenance page, effort S)
- Seen: The static page uses d.toLocaleString(), giving '11/5/2026, 1:09:27 PM' with seconds (static_maintenance__with_status_message__desktop.png). The in-app page shows 'Nov 5, 1:09 PM' (maintenance_page__with_message__desktop.png). The comment at line 129 says the two are meant to stay consistent.
- Change: Pass the same options as MaintenancePage.tsx: month short, day numeric, hour numeric, minute 2-digit.

## Light mode

Munin is dark only today. No light mode work has started.

### Decisions made

- Dark stays the default, with light and system as options.
- The preference is per browser, in a cookie shared across the subdomains. Production
  serves chat, docs, search, research, upload and auth separately, so localStorage
  cannot share it.
- The toggle goes in Settings and in the static page headers.
- Code blocks follow the theme.
- The protein viewer stays dark in both themes.
- The login mail stays dark, because mail clients cannot see the site preference.
- The demo deck follows the presenter's theme.
- All pages ship together, so moving between sites never switches theme.
- Filed chats stay out of the sidebar's date list.

### Estimate

4 to 6 person-days for all surfaces, including testing. The colour swap is the small
part. Most of the cost is the cross-subdomain preference, code and chart palettes, and
checking every screen in both themes.

| Work | Days |
|---|---|
| Theme base, aliases, palette in both CSS files | 0.25-0.5 |
| Hard-coded replacements, dark assumptions, logo, vortex, artifact backing | 0.25-0.5 |
| Toggle, theme state | 0.25-0.5 |
| Cross-subdomain cookie, 11 page heads, theme-color, service worker | 0.5-1 |
| Syntax theme and print fixes | 0.5 |
| Metrics and embedding map palettes | 0.5 |
| Protein viewer and widget theme channel | 0.25-0.5 |
| E2E fixes, light project, contrast guard | 0.5-1 |
| Visual QA of every screen in both themes, mobile, fixes | 0.5-1 |
| Three-part deploy and rollback check | 0.25 |
| **Total** | **3.75-6.25** |

### Proposed palette

| Token | Dark | Light | Contrast notes |
|---|---|---|---|
| `bg-primary` | `#0f1419` | `#ffffff` | Base surface. Figures for each foreground are in its own row, in the order bg-primary / bg-secondary / bg-tertiary. |
| `bg-secondary` | `#1a1f26` | `#f6f8fa` | Same cool blue-grey family. Sidebar and cards sit slightly darker than the page, where dark raises them lighter. In the screenshots this still separates clearly. |
| `bg-tertiary` | `#242a33` | `#e9edf1` | Slightly darker than a stock GitHub-style #eaeef2, so that every text token stays above 4.5:1. |
| `text-primary` | `#e6edf3` | `#1f2429` | 15.64 / 14.69 / 13.29:1. Passes. Dark is 15.67 / 14.02 / 12.22. |
| `text-secondary` | `#8b949e` | `#57606a` | 6.39 / 6.00 / 5.43:1. Passes. Dark is 6.02 / 5.38 / 4.70. |
| `accent` | `#58a6ff` | `#0a5cb8` | 6.49 / 6.10 / 5.52:1. Passes. text-bg-primary on a solid accent is 6.49 (dark 7.33). Text on its own /15 tint is 5.17 / 4.88 / 4.43 (dark 5.77 / 5.08 / 4.38). Started from print's #0b62c4 and darkened until the tints pass on primary and secondary. Browser-measured accent/20 was 4.48, and 4.10 on the avatar. Dark measured 4.02 on the avatar. |
| `accent-hover` | `#79b8ff` | `#084c99` | 8.39 / 7.88 / 7.13:1. Hover goes darker here and lighter in dark, in both cases adding contrast. |
| `border` | `#30363d` | `#d0d7de` | 1.45 / 1.36 / 1.23:1. Decorative, and matches dark's 1.52 / 1.36 / 1.18. |
| `border-input (static only today)` | `#6e7681` | `#6e7781` | 4.55 / 4.27 / 3.86:1, above the 3:1 needed for form controls. Dark is 4.03 / 3.61 / 3.14. Add it to the SPA as well. |
| `success` | `#3fb950` | `#196b2e` | 6.60 / 6.20 / 5.61:1. Passes. /15 tint is 5.27 / 4.98 / 4.55 (dark 5.78 / 5.09 / 4.45). |
| `warning` | `#d29922` | `#7a5300` | 6.85 / 6.44 / 5.82:1. Passes. /15 tint is 5.46 / 5.15 / 4.71 (dark 5.84 / 5.13 / 4.44). Reads brown-amber, which is what amber needs in order to pass on white. |
| `error` | `#f85149` | `#b81c28` | 6.49 / 6.10 / 5.52:1. Passes, and improves on dark's 5.52 / 4.94 / 4.31. /15 tint is 5.02 / 4.74 / 4.33 (dark 4.70 / 4.18 / 3.64). Tints on bg-tertiary stay under 4.5 for accent and error in light, but dark is lower or close to the same. |

### New tokens needed

- SPA aliases: --bg-primary, --bg-secondary, --bg-tertiary, --text-primary, --text-secondary, --accent, --accent-hover, --border, --border-input, --success, --warning, --error, each pointing at --color-*. This fixes the support widget, Settings.tsx:455 and feather-vortex.js in one place.
- --color-border-input in the SPA.
- --color-scrim for the support widget overlay, and optionally to unify bg-black/50, bg-black/60 and bg-bg-primary/70.
- --code-* set, about 11 values (base, comment, keyword, string, function, number and so on). Used as var() inside the highlighter style object, so inline colours follow the theme without a re-render and fix print too.
- --chart-1 to --chart-12. Dark keeps today's values. Proposed light: #0b62c4 #1a7f37 #9a6700 #cf222e #8250df #b35900 #0e7f8a #d1397a #3b5bdb #5a7d00 #bc4c00 #6e7781, lowest 4.31:1 on #f6f8fa.
- Light cluster palette in JS for deck.gl. On #e9edf1, 19 of 20 current dots fall under 3:1 at their current alpha (6 in dark). Scaling RGB by 0.6 and drawing opaque gives 0 under 3:1, lowest 3.45, so a hand-tuned set should beat a straight scale. Update deck.gl's updateTriggers so a toggle recolours existing points.
- --logo-filter: brightness(0) invert(1) in dark, none in light. The source webp is a black raven.
- --viewer-bg for ProteinViewer and proteinRuntime, if the viewer follows the theme.
- --accent-2 for the landing gradient: #a371f7 in dark, about #8250df in light.

### Work beyond colours

The audit's notes. Where they ask about a choice, the decisions above win.

- **Theme switch and CSS base** (S). `frontend/webui/src/index.css, frontend/static/shared/style.css`. Tailwind v4 output is compatible: the built CSS uses var() inside color-mix, with static hex only as a fallback for old browsers, and runtime overrides worked in my test. Add a :root[data-theme=light] block in both files. They use different variable names and must be kept in sync. Add color-scheme too, since none is set anywhere in the app today.
- **Shared preference across sites** (M). `frontend/caddy/Caddyfile, frontend/caddy/Caddyfile.local, frontend/auth/main.py, all page heads`. Production is not one origin. Caddyfile serves root, auth, docs, chat, search, research and upload as separate subdomains, so localStorage cannot share the preference. Only Caddyfile.local is single-origin. Use a JS-readable munin_theme cookie on .<domain>, like the session cookie's scope but not HttpOnly, with no Domain attribute on localhost. Pages need a way to work out the domain. Cookie changes fire no storage event, so re-read on focus for other tabs.
- **First-paint script** (M). `frontend/webui/index.html, static landing/search/research/upload/maintenance, 3 static/docs pages, auth login.html and verify.html (11 entry points)`. A blocking inline head script that sets data-theme before CSS, in all 11 files, because each page has its own head. Avoid {{ in it, since Caddy templates and Jinja parse these files. Add a test that the copies match.
- **Service worker, manifest, theme-color** (S). `frontend/webui/public/sw.js, frontend/webui/public/manifest.json, frontend/webui/index.html, frontend/webui/src/serviceWorker.test.ts`. Navigation is network-first and HTML is never stored, so the inline script stays fresh and the worker does not cause flash. /shared is network-first too. /manifest.json is cache-first, so bump munin-v3 to v4 if it changes, and bump ?v=3 on the shared scripts. The manifest holds one colour, so a PWA launch splash can mismatch. The apple status-bar value black-translucent needs review. sw.js must be included in the deploy.
- **Toggle UI and theme state** (M). `frontend/webui/src/components/Settings.tsx, frontend/webui/src/components/Sidebar.tsx, a new store or hook in frontend/webui/src/stores, static page headers`. A light / dark / system control plus a useTheme hook, which deck.gl and the widget need. Each static page has its own header copy, so a static toggle means editing each or injecting one from /shared.
- **Dark assumptions in token use** (S). `PlanCard.tsx, MemoryProposalPill.tsx, ChatInput.tsx, Sidebar.tsx, ReportDialog.tsx, App.tsx, admin/UsersTab.tsx, admin/GroupsTab.tsx, support-widget.js`. text-white on bg-accent fails today at 2.53:1, so move it to text-bg-primary. The 19 existing text-bg-primary uses flip correctly. Scrims mix black/50, black/60 and bg-primary/70, and the bg-primary ones become white veils in light. App.tsx:612 shows the black logo with no filter on the dark header, an existing bug that light mode happens to hide. Other opacity tints passed where tested.
- **Syntax highlighting, maths, mermaid** (M). `frontend/webui/src/components/Markdown.tsx, frontend/webui/src/components/ArtifactPanel.tsx:439`. oneDark only has its background overridden. On #f6f8fa its tokens measured 1.89 to 2.77:1. Stock replacements are weak too: one-light has 7 of 11 colours under 4.5, lowest 2.42. coldark-cold has 1 of 12, though I did not check where that colour is used. Prefer var()-based tokens. ArtifactPanel uses oneDark raw with its own dark background, which is a separate fix. KaTeX inherits currentColor and rendered correctly. Mermaid is not a dependency, so nothing renders it.
- **Print to PDF** (S). `frontend/webui/src/lib/printPdf.ts`. You assumed it could stay; that is only half right. Its comment says highlight styles survive, and they do: 8 of 11 oneDark colours are under 4.5 on white, lowest 2.02. That is an existing bug, and code artifacts should also carry oneDark's dark box into the PDF (read from the code, print not rendered). It omits success, warning and error. New risk: a :root:not([data-theme]) rule under prefers-color-scheme would outrank its :root override in the popup on a light OS, so set a data-theme on the print document. Any new tokens also need print values.
- **Charts** (M). `frontend/webui/src/components/admin/MetricsTab.tsx, frontend/webui/src/components/EmbeddingMapView.tsx`. MetricsTab can use var() strings and a second series list, including legend text. EmbeddingMapView draws RGB arrays in WebGL, so it needs a JS palette keyed on theme state, updateTriggers, and a look at the map background. The chips at line 203 share the palette. I did not run the map in the test.
- **ProteinViewer and widget theming** (M). `frontend/webui/src/components/ProteinViewer.tsx, frontend/webui/src/components/WidgetFrame.tsx, frontend/webui/src/widgets/proteinRuntime.js, frontend/webui/src/lib/widgetDocument.ts`. The iframe is sandboxed allow-scripts with an opaque origin and a strict CSP, so it has no cookie, storage or network, and it inherits no CSS. prefers-color-scheme sees only the OS, not the toggle. postMessage is the only channel. Have WidgetFrame send theme alongside state, on ready and on change, so future widgets get it free. The runtime should call the 3Dmol background setter (3Dmol has one; not tried here) and set the body background. Do not bake the theme into srcDoc: viewerDocument is a cached singleton, and a new document would drop the live 3D context. If the viewer stays dark, 'Loading structure...' and errors on the fixed #0f1419 drop to 2.9 and 3.08:1, so they need fixed light text.
- **Artifacts and images** (S). `frontend/webui/src/components/ArtifactPanel.tsx, frontend/webui/src/components/InlineArtifacts.tsx, frontend/webui/src/lib/artifactDisplay.ts`. HTML artifacts are never rendered. There is no srcDoc, and the panel showed source as text in my test, so that risk does not exist today. Inline previews always have white backing. ArtifactPanel.tsx:411 does not, including SVGs with an external_url, so transparent images change with the theme. Add bg-white there. A grep found no dark-background instruction for plots: compute_agent.py only says to savefig, so default plots are opaque white. Plots the model chooses to make dark will sit on white and look fine. White-on-transparent ones would need a checker or manual toggle.
- **FeatherVortex and logo** (S). `frontend/static/shared/feather-vortex.js, frontend/webui/src/components/FeatherVortex.tsx, login.html, verify.html, static/shared/style.css, Sidebar.tsx, App.tsx`. The shared canvas script runs on the SPA, login, verify, maintenance, research and search pages. Read --text-primary and --text-secondary per frame so a toggle mid-animation works. It depends on the SPA aliases. Replace the 4 filters with --logo-filter. munin_logo_with_script_white and the other white assets are unreferenced, so CSS is the simpler route.
- **Static pages and Uppy** (S). `frontend/static/upload/index.html, frontend/static/landing/index.html`. 9 pages flipped with 0 text failures in the default state. Uppy needs its theme option set from the preference and a check with real CSS, which I blocked. Search results and research states need a look.
- **DemoDeck** (S). `frontend/webui/src/components/DemoDeck.tsx, frontend/webui/public/demo`. It is token-only with no hard-coded colours, and slides 1 to 4 flipped cleanly. Only the shared 'Next' disabled state fails, and it fails in dark too. The photo is unaffected. The work is the decision on whether presentations pin a theme, plus one line to pin it.
- **E2E and regression guard** (M). `frontend/webui/playwright.config.ts, frontend/webui/e2e/responsive.spec.ts, frontend/webui/e2e/widgets.spec.ts, a new theme spec`. 4 assertions pin rgb(88, 166, 255). Playwright emulates light by default and the config sets no colorScheme, so a follow-OS default should turn them red (reasoned, not run). Pin colorScheme dark, or read the variable instead. Add a light project and turn the scratch contrast walker into a test, with a toggle, no-flash and print check.
- **Rollout** (S). `frontend/webui build output, frontend/static, frontend/auth image`. The auth templates are baked into the image, and the static files and SPA bundle deploy separately, including sw.js. There will be a period where sites disagree unless shared files go first, as the deploy notes already say.

### Corrections to the audit

A second check found these, and they override the notes above.

- Caddyfile.local is not single-origin. It serves separate ports (:80, :8081 search, :8082 docs, :8083 landing, :8084 upload, :8085), so localStorage is not shared there either. The cookie with no Domain attribute on localhost still works, because cookies ignore ports, so the recommendation stands but 'Only Caddyfile.local is single-origin' is wrong.
- Proposed --chart-1 to 12: the lowest ratio on #f6f8fa is 4.27 (#6e7781), not 4.31. Three colours are under 4.5 (#0e7f8a 4.46, #d1397a 4.31, #6e7781 4.27), and legend text uses them, so the set needs tuning for text.
- E2E: 4 occurrences of rgb(88, 166, 255) is right, but responsive.spec.ts:168 is a not.toHaveCSS on border-top-color. In light it keeps passing rather than going red, and stops guarding anything. Only 3 assertions would fail.
- Cluster dots in dark: I get 7 of 20 under 3:1 at the current alpha on #242a33, not 6. Light (19) and the scaled palette (0 under 3:1, lowest 3.45) match.
- Possibly missed entry point: frontend/static/chat/index.html is tracked, is served at chat.<domain> (Caddyfile line 107), and has its own theme-color #0f1419 and black-translucent meta. It looks like the webui build output. If the build does not regenerate it, it is a 12th head to change. The audit's 11 entry points and colour rows do not mention it.
- Checked and correct: text-primary and text-secondary ratios in both themes, accent, status colours, border and /15 tints (within rounding; dark error tint on bg-secondary is 4.15 by my calculation against 4.18 stated). Also correct: white on accent 2.53, print colours 2.54/2.52/3.35, the 151 reclassification and 82 total, the estimate table sums, and the non-colour items spot-checked in the named files.
