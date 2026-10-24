"""Styles and script for WealthRyse Next (see web_next.py for the switch).

The new design keeps the classic visual language: it styles everything with the
classic theme's custom properties (--ink, --bg, --line, --font-mission and so on),
so the existing light and dark themes, Barlow capitals and square corners carry
over. What changes is the page structure: a side rail and top bar instead of the
header, a home briefing, search, details for headline figures and a phone layout.
The script only rearranges and restyles; it reads the same endpoints the classic
pages call and never computes a new figure.
"""

NEXT_CSS = r"""
:root { --nx-rail: 64px; --nx-rail-open: 248px; --nx-rail-mini: 64px; --nx-top: 60px; --nx-bar-bg: #000; --nx-bar-ink: #fff; --nx-bar-muted: rgba(255, 255, 255, .62); --nx-bar-line: rgba(255, 255, 255, .12); --nx-cert: #0f7060; }
@media screen { :root { --nx-cert: #6fd3ae; } }
@view-transition { navigation: auto; }
::view-transition-old(root), ::view-transition-new(root) { animation-duration: .16s; }
@media (prefers-reduced-motion: reduce) { @view-transition { navigation: none; } }

/* Shell: a fixed black rail on the left and a black top bar, in both themes, like the classic header. */
html[data-design="next"] body > header { display: none; }
html[data-design="next"] body { padding-top: var(--nx-top); padding-left: var(--nx-rail); }
html[data-design="next"] main { width: min(1320px, calc(100% - 56px)); padding-top: 30px; }
html[data-design="next"] .breadcrumbs, html[data-design="next"] .account-sticky .account-nav, html[data-design="next"] .account-switcher { display: none !important; }
.nx-rail { position: fixed; inset: 0 auto 0 0; z-index: 55; width: var(--nx-rail); display: flex; flex-direction: column; gap: 18px; padding: 16px 0 18px; overflow-y: auto; color: var(--nx-bar-ink); background: var(--nx-bar-bg); border-right: 1px solid var(--nx-bar-line); font-family: var(--font-mission); view-transition-name: nx-rail; scrollbar-width: thin; }
.nx-brand { display: flex; align-items: center; gap: 12px; padding: 0 20px; color: #fff; text-decoration: none; }
.nx-brand img { width: 38px; height: 38px; border: 1px solid rgba(255, 255, 255, .22); border-radius: 4px; }
.nx-brand span { font-size: 12px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; }
.nx-switch { display: grid; gap: 3px; margin: 0 14px; padding: 10px 12px; color: #fff; text-align: left; background: transparent; border: 1px solid var(--nx-bar-line); border-radius: 0; cursor: pointer; font: inherit; }
.nx-switch:hover, .nx-switch:focus-visible { border-color: rgba(255, 255, 255, .5); }
.nx-switch small { color: var(--nx-bar-muted); font-size: 10px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; }
.nx-switch b { display: flex; justify-content: space-between; gap: 8px; font-size: 14px; font-weight: 600; letter-spacing: .02em; }
.nx-switch b::after { content: "⇅"; color: var(--nx-bar-muted); font-weight: 400; }
.nx-nav { display: grid; gap: 1px; }
.nx-group { padding: 14px 20px 6px; color: rgba(255, 255, 255, .42); font-size: 10px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; }
.nx-link { position: relative; display: flex; align-items: center; gap: 12px; min-height: 38px; padding: 7px 20px; color: var(--nx-bar-muted); font-size: 12px; font-weight: 600; letter-spacing: .14em; text-decoration: none; text-transform: uppercase; }
.nx-link svg { flex: none; width: 17px; height: 17px; stroke: currentColor; fill: none; stroke-width: 1.6; opacity: .85; }
.nx-link:hover, .nx-link:focus-visible { color: #fff; outline: none; background: rgba(255, 255, 255, .05); }
.nx-link[aria-current="page"], .nx-link.is-open { color: #fff; }
.nx-link[aria-current="page"]::before { position: absolute; left: 0; top: 6px; bottom: 6px; width: 2px; content: ""; background: #fff; }
.nx-link .nx-count { margin-left: auto; min-width: 18px; padding: 0 5px; color: #000; background: #fff; font-size: 10px; letter-spacing: 0; text-align: center; }
.nx-sub { display: grid; padding: 2px 0 6px; }
.nx-sub a { padding: 6px 20px 6px 49px; color: rgba(255, 255, 255, .55); font-size: 11px; font-weight: 600; letter-spacing: .12em; text-decoration: none; text-transform: uppercase; }
.nx-sub a:hover, .nx-sub a:focus-visible { color: #fff; outline: none; }
.nx-sub a[aria-current="page"] { color: #fff; box-shadow: inset 2px 0 0 #fff; }
/* Mini rail: icons only, opens over the page on hover or focus, or stays open when pinned. */
html.nx-pinned { --nx-rail: var(--nx-rail-open); }
.nx-rail { width: var(--nx-rail); overflow-x: hidden; transition: width .16s ease, box-shadow .16s ease; }
.nx-rail.is-peek { width: var(--nx-rail-open); box-shadow: 18px 0 40px rgba(0, 0, 0, .45); }
.nx-link span, .nx-brand span, .nx-group, .nx-sub a, .nx-rail-foot { white-space: nowrap; }
.nx-rail-pin { display: flex; align-items: center; gap: 12px; min-height: 38px; margin-top: auto; padding: 7px 20px; color: var(--nx-bar-muted); background: transparent; border: 0; border-top: 1px solid var(--nx-bar-line); cursor: pointer; font: inherit; font-size: 11px; font-weight: 600; letter-spacing: .14em; text-align: left; text-transform: uppercase; }
.nx-rail-pin:hover, .nx-rail-pin:focus-visible { color: #fff; outline: none; background: rgba(255, 255, 255, .05); }
.nx-rail-pin svg { flex: none; width: 17px; height: 17px; stroke: currentColor; fill: none; stroke-width: 1.6; transition: transform .16s ease; }
html.nx-pinned .nx-rail-pin svg { transform: scaleX(-1); }
.nx-rail-pin kbd { margin-left: auto; padding: 0 5px; color: var(--nx-bar-muted); border: 1px solid var(--nx-bar-line); font: inherit; font-size: 10px; }
.nx-rail-pin + .nx-rail-foot { margin-top: 0; }
html:not(.nx-pinned) .nx-rail:not(.is-peek) :is(.nx-brand span, .nx-link span, .nx-sub, .nx-rail-foot, .nx-switch, .nx-rail-pin span, .nx-rail-pin kbd, .nx-link .nx-count) { display: none; }
html:not(.nx-pinned) .nx-rail:not(.is-peek) .nx-brand { justify-content: center; padding: 0; }
html:not(.nx-pinned) .nx-rail:not(.is-peek) :is(.nx-link, .nx-rail-pin) { justify-content: center; padding-left: 0; padding-right: 0; }
html:not(.nx-pinned) .nx-rail:not(.is-peek) .nx-group { height: 1px; margin: 10px 16px; padding: 0; overflow: hidden; font-size: 0; background: var(--nx-bar-line); }
html:not(.nx-pinned) .nx-rail:not(.is-peek) .nx-link[data-agent-inbox]:has(.nx-count)::after { position: absolute; top: 8px; left: 50%; width: 6px; height: 6px; margin-left: 6px; content: ""; background: #fff; border-radius: 50%; }
html:not(.nx-pinned) .nx-top .nx-switch { display: flex; align-items: center; gap: 8px; flex: 0 1 240px; min-width: 0; margin: 0; padding: 6px 10px; }
html:not(.nx-pinned) .nx-top .nx-switch small { display: none; }
html:not(.nx-pinned) .nx-top .nx-switch b { width: 100%; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* The open section's pages, as tabs above the content (the open rail lists them too). */
.nx-subtabs { display: flex; gap: 22px; margin: 0 0 4px; overflow-x: auto; border-bottom: 1px solid var(--line); font-family: var(--font-mission); scrollbar-width: none; }
.nx-subtabs a { flex: none; padding: 10px 0 9px; color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .14em; text-decoration: none; text-transform: uppercase; border-bottom: 2px solid transparent; margin-bottom: -1px; }
.nx-subtabs a:hover, .nx-subtabs a:focus-visible { color: var(--ink); outline: none; }
.nx-subtabs a[aria-current="page"] { color: var(--ink); border-bottom-color: var(--ink); }
@media (min-width: 1024px) { html.nx-pinned .nx-subtabs { display: none; } }
@media (max-width: 1023px) { .nx-subtabs { display: none; } }
@media (prefers-reduced-motion: reduce) { .nx-rail, .nx-rail-pin svg { transition: none; } }
.nx-rail-foot { margin-top: auto; padding: 14px 20px 0; border-top: 1px solid var(--nx-bar-line); color: var(--nx-bar-muted); font-size: 11px; letter-spacing: .06em; overflow-wrap: anywhere; }
.nx-top { position: fixed; top: 0; right: 0; left: var(--nx-rail); z-index: 50; height: var(--nx-top); display: flex; align-items: center; gap: 14px; padding: 0 28px; color: var(--nx-bar-ink); background: #000; border-bottom: 1px solid var(--nx-bar-line); font-family: var(--font-mission); view-transition-name: nx-top; }
.nx-top .nx-brand, .nx-top .nx-switch { display: none; padding: 0; }
.nx-search { flex: 1 1 auto; max-width: 460px; display: flex; align-items: center; gap: 10px; min-height: 38px; padding: 0 12px; color: var(--nx-bar-muted); background: transparent; border: 1px solid var(--nx-bar-line); border-radius: 0; cursor: pointer; font: inherit; font-size: 13px; text-align: left; }
.nx-search:hover, .nx-search:focus-visible { color: #fff; border-color: rgba(255, 255, 255, .5); outline: none; }
.nx-search svg { width: 16px; height: 16px; stroke: currentColor; fill: none; stroke-width: 1.8; flex: none; }
.nx-search .nx-search-text { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nx-search kbd, .nx-palette kbd { padding: 1px 6px; color: inherit; border: 1px solid currentColor; border-radius: 0; font: 600 10px var(--font-mission); letter-spacing: .08em; opacity: .7; }
.nx-status { display: inline-flex; align-items: center; gap: 8px; min-height: 30px; padding: 0 12px; color: #fff; background: transparent; border: 1px solid var(--nx-bar-line); border-radius: 0; cursor: pointer; font: 600 11px var(--font-mission); letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; }
.nx-status::before { width: 7px; height: 7px; content: ""; background: #6fd3ae; border-radius: 50%; }
.nx-status.late::before { background: #f2cf73; }
.nx-status.stale::before { background: #ff7b72; }
.nx-spacer { flex: 1 1 0; }
.nx-user { position: relative; }
.nx-user > button { display: grid; place-items: center; width: 36px; height: 36px; color: #fff; background: transparent; border: 1px solid var(--nx-bar-line); border-radius: 50%; cursor: pointer; font: 700 13px var(--font-mission); text-transform: uppercase; }
.nx-user > button:hover, .nx-user > button:focus-visible { border-color: #fff; outline: none; }
.nx-menu { position: absolute; top: calc(100% + 10px); right: 0; z-index: 70; display: grid; min-width: 250px; padding: 8px 0; color: #fff; background: #000; border: 1px solid rgba(255, 255, 255, .18); }
.nx-menu[hidden] { display: none; }
.nx-menu .nx-menu-who { padding: 8px 16px 12px; border-bottom: 1px solid var(--nx-bar-line); color: var(--nx-bar-muted); font-size: 12px; overflow-wrap: anywhere; }
.nx-menu .nx-menu-who b { display: block; color: #fff; font-size: 13px; font-weight: 600; }
.nx-menu a, .nx-menu button { display: block; width: 100%; padding: 10px 16px; color: rgba(255, 255, 255, .78); background: none; border: 0; cursor: pointer; font: 600 11px var(--font-mission); letter-spacing: .14em; text-align: left; text-decoration: none; text-transform: uppercase; }
.nx-menu a:hover, .nx-menu button:hover, .nx-menu a:focus-visible, .nx-menu button:focus-visible { color: #fff; background: rgba(255, 255, 255, .08); outline: none; }
.nx-tabbar { display: none; }

/* Account page header: the name leads; the IBKR number and reporting currency follow. */
html[data-design="next"] .account-hero { align-items: flex-end; gap: 24px; }
html[data-design="next"] .account-hero h1 { font-size: clamp(28px, 3.4vw, 44px); }
.nx-id { color: var(--muted-strong); font-weight: 600; letter-spacing: .08em; }
html[data-design="next"] .account-hero-controls .asof-control { min-width: 220px; }

/* One-sentence summaries that restate figures already on the page. */
.nx-brief { max-width: 860px; margin: 0; color: var(--muted-strong); font-size: 18px; line-height: 1.5; letter-spacing: .01em; }
.nx-brief b { color: var(--ink); font-weight: 600; }
.nx-brief b.gain { color: var(--gain); }
.nx-brief b.loss { color: var(--loss); }
.nx-brief .gain, .nx-brief .loss { font-weight: 600; }
.page-view .nx-brief { grid-column: 1 / -1; margin: 4px 0 2px; }

/* Home briefing. */
html[data-design="next"] .home-hero, html[data-design="next"] main > .accounts { display: none !important; }
.nx-home { display: grid; gap: 22px; }
.nx-home-head { display: grid; gap: 10px; }
.nx-eyebrow { display: inline-flex; align-items: center; gap: 12px; color: var(--muted-strong); font-size: 12px; font-weight: 600; letter-spacing: .22em; text-transform: uppercase; }
.nx-eyebrow::before { width: 32px; height: 1px; content: ""; background: currentColor; }
.nx-home h1 { font-size: clamp(34px, 4.6vw, 60px); }
.nx-table-wrap { overflow-x: auto; border-top: 1px solid var(--line-strong); }
.nx-accounts { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
.nx-accounts th { padding: 14px 12px; color: var(--muted); background: transparent; border-bottom: 1px solid var(--line); font-size: 10px; font-weight: 600; letter-spacing: .18em; text-align: right; text-transform: uppercase; white-space: nowrap; }
.nx-accounts th:first-child, .nx-accounts td:first-child { text-align: left; }
.nx-accounts th button { padding: 0; color: inherit; background: none; border: 0; cursor: pointer; font: inherit; letter-spacing: inherit; text-transform: inherit; }
.nx-accounts th button:hover, .nx-accounts th button:focus-visible { color: var(--ink); }
.nx-accounts th[aria-sort="descending"] button::after { content: " ↓"; }
.nx-accounts th[aria-sort="ascending"] button::after { content: " ↑"; }
.nx-accounts td { padding: 18px 12px; border-bottom: 1px solid var(--line); font-size: 16px; text-align: right; white-space: nowrap; }
.nx-accounts td.c-name { width: 34%; white-space: normal; }
.nx-accounts tbody tr { position: relative; }
.nx-accounts tbody tr:hover { background: var(--mission-hover); }
.nx-accounts .nx-grp td { padding: 26px 12px 8px; color: var(--muted); border-bottom: 1px solid var(--line); font-size: 10px; font-weight: 600; letter-spacing: .22em; text-align: left; text-transform: uppercase; }
.nx-acct { display: flex; align-items: flex-start; gap: 10px; }
.nx-acct a { display: grid; gap: 4px; color: var(--ink); text-decoration: none; }
.nx-acct a::after { position: absolute; inset: 0; content: ""; }
.nx-acct a b { font-size: 17px; font-weight: 600; letter-spacing: .01em; white-space: normal; }
.nx-acct a small, .nx-sub-line { color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; }
.nx-pin { position: relative; z-index: 2; width: 26px; height: 26px; margin-top: -1px; color: var(--muted); background: none; border: 1px solid transparent; cursor: pointer; font-size: 15px; line-height: 1; }
.nx-pin:hover, .nx-pin:focus-visible { color: var(--ink); border-color: var(--line-strong); outline: none; }
.nx-pin[aria-pressed="true"] { color: var(--ink); }
.nx-nav-cell { font-size: 18px !important; font-weight: 500; }
.nx-spark { display: block; width: 120px; height: 30px; margin-left: auto; }
.nx-spark path.line { fill: none; stroke: var(--chart-portfolio); stroke-width: 1.4; }
.nx-spark path.area { fill: var(--chart-portfolio); fill-opacity: .07; stroke: none; }
.nx-spark circle { fill: var(--chart-portfolio); }
.nx-state { display: inline-flex; align-items: center; gap: 7px; color: var(--muted-strong); font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }
.nx-state::before { width: 6px; height: 6px; content: ""; background: var(--muted); border-radius: 50%; }
.nx-state.cert::before { background: var(--nx-cert); }
.nx-state.cert { color: var(--nx-cert); }
.nx-state.behind { color: var(--warn-ink); }
.nx-state.behind::before { background: var(--warn-ink); }
.nx-skel { display: inline-block; width: 70%; height: 12px; background: linear-gradient(90deg, var(--line), var(--line-strong), var(--line)); background-size: 200% 100%; animation: nx-shimmer 1.4s linear infinite; vertical-align: middle; }
@keyframes nx-shimmer { to { background-position: -200% 0; } }
.nx-empty { padding: 26px 0; color: var(--muted); }

/* Figure details: a click or tap on a headline figure shows what it is and where it comes from. */
html[data-design="next"] .metric[data-nx-info], html[data-design="next"] #nav[data-nx-info] { cursor: help; }
html[data-design="next"] .metric[data-nx-info]:hover .value, html[data-design="next"] #nav[data-nx-info]:hover { text-decoration: underline dotted var(--muted); text-underline-offset: 6px; text-decoration-thickness: 1px; }
.nx-pop { position: absolute; z-index: 80; width: min(340px, calc(100vw - 32px)); display: grid; gap: 10px; padding: 16px; color: var(--ink); background: var(--bg); border: 1px solid var(--line-strong); box-shadow: 0 18px 44px rgba(0, 0, 0, .35); font-family: var(--font-mission); font-size: 13px; }
.nx-pop h4 { margin: 0; color: var(--muted-strong); font-size: 11px; font-weight: 600; letter-spacing: .18em; text-transform: uppercase; }
.nx-pop .nx-pop-value { font-size: 24px; font-weight: 500; }
.nx-pop dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px 14px; margin: 0; }
.nx-pop dt { color: var(--muted); font-size: 10px; font-weight: 600; letter-spacing: .16em; line-height: 1.9; text-transform: uppercase; }
.nx-pop dd { margin: 0; }
.nx-pop a { color: var(--ink); font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; text-underline-offset: 4px; }

/* Search or ask. */
.nx-palette { width: min(680px, calc(100vw - 24px)); max-height: min(560px, calc(100vh - 120px)); margin: 90px auto auto; padding: 0; color: var(--ink); background: var(--bg); border: 1px solid var(--line-strong); border-radius: 0; box-shadow: 0 30px 80px rgba(0, 0, 0, .5); font-family: var(--font-mission); }
.nx-palette::backdrop, .nx-sheet::backdrop { background: rgba(0, 0, 0, .55); }
.nx-palette form { display: flex; align-items: center; gap: 12px; padding: 14px 18px; border-bottom: 1px solid var(--line); }
.nx-palette input { flex: 1; min-width: 0; padding: 6px 0; color: var(--ink); background: transparent; border: 0; box-shadow: none; font: 500 18px var(--font-mission); outline: none; }
.nx-palette input:focus-visible { box-shadow: none; }
.nx-results { max-height: 420px; overflow-y: auto; padding: 6px 0 10px; }
.nx-results h5 { margin: 0; padding: 12px 18px 6px; color: var(--muted); font-size: 10px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; }
.nx-result { display: flex; align-items: center; gap: 12px; padding: 10px 18px; color: var(--ink-soft); cursor: pointer; font-size: 15px; text-decoration: none; }
.nx-result small { margin-left: auto; padding-left: 12px; color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; white-space: nowrap; }
.nx-result[aria-selected="true"] { color: var(--ink); background: var(--mission-raised); box-shadow: inset 2px 0 0 var(--ink); }
.nx-result mark { color: inherit; background: none; text-decoration: underline; text-underline-offset: 3px; }
.nx-palette-foot { display: flex; flex-wrap: wrap; gap: 18px; padding: 10px 18px; color: var(--muted); border-top: 1px solid var(--line); font-size: 11px; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; }

/* Phone sheets (account switcher and More). */
.nx-sheet { width: 100%; max-width: 560px; max-height: 80vh; margin: auto auto 0; padding: 0 0 env(safe-area-inset-bottom, 0px); color: #fff; background: #000; border: 1px solid rgba(255, 255, 255, .18); border-bottom: 0; border-radius: 0; font-family: var(--font-mission); }
.nx-sheet-head { display: flex; align-items: center; justify-content: space-between; padding: 14px 20px; border-bottom: 1px solid var(--nx-bar-line); font-size: 11px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; }
.nx-sheet-head button { color: #fff; background: none; border: 0; font-size: 20px; cursor: pointer; min-width: 44px; min-height: 44px; }
.nx-sheet .nx-nav { padding: 6px 0 14px; }

/* Restated details in the new layout. */
html[data-design="next"] .card { scroll-margin-top: calc(var(--nx-top) + 20px); }
html[data-design="next"] .page-view > .card[id] { scroll-margin-top: calc(var(--nx-top) + 20px); }
html[data-design="next"] .label { font-size: 11px; }
html[data-design="next"] th { font-size: 10.5px; }
body .wr-fab { z-index: 52; }

@media (max-width: 1023px) {
  :root { --nx-top: 58px; }
  html[data-design="next"] body { padding-left: 0; padding-bottom: calc(66px + env(safe-area-inset-bottom, 0px)); }
  html[data-design="next"] main { width: min(100% - 32px, 1320px); padding-top: 22px; }
  .nx-rail { display: none; }
  .nx-top { left: 0; gap: 10px; padding: 0 14px; }
  .nx-top .nx-brand, .nx-top .nx-switch { display: flex; }
  .nx-top .nx-switch { display: grid; }
  .nx-top .nx-brand img { width: 34px; height: 34px; }
  .nx-top .nx-brand span, .nx-status, .nx-search .nx-search-text, .nx-search kbd { display: none; }
  .nx-top .nx-switch { flex: 1 1 auto; min-width: 0; margin: 0; padding: 6px 10px; }
  .nx-top .nx-switch b { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .nx-search { flex: 0 0 auto; width: 40px; min-height: 40px; justify-content: center; padding: 0; }
  .nx-top .nx-spacer { order: 2; }
  .nx-top .nx-search { order: 3; }
  .nx-top .nx-user { order: 4; }
  html[data-design="next"] .account-hero h1 { font-size: 26px; }
  html[data-design="next"] .account-hero-controls .asof-control { display: flex !important; flex-direction: row !important; align-items: center; gap: 12px; min-width: 0; text-align: left; }
  html[data-design="next"] .account-hero-controls .asof-control .asof-select { flex: 1; min-height: 40px; }
  .nx-tabbar { position: fixed; inset: auto 0 0 0; z-index: 50; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); padding: 4px 4px env(safe-area-inset-bottom, 0px); background: #000; border-top: 1px solid var(--nx-bar-line); font-family: var(--font-mission); }
  .nx-tabbar a, .nx-tabbar button { display: grid; justify-items: center; align-content: center; gap: 4px; min-height: 56px; padding: 4px 0; color: var(--nx-bar-muted); background: none; border: 0; cursor: pointer; font: 600 9.5px var(--font-mission); letter-spacing: .06em; text-decoration: none; text-transform: uppercase; }
  .nx-tabbar svg { width: 20px; height: 20px; stroke: currentColor; fill: none; stroke-width: 1.6; }
  .nx-tabbar [aria-current="page"] { color: #fff; box-shadow: inset 0 2px 0 #fff; }
  body .wr-fab { bottom: calc(78px + env(safe-area-inset-bottom, 0px)) !important; }
  .account-sticky { position: static !important; margin-top: 18px !important; }
  html[data-design="next"] .account-hero { flex-direction: column; align-items: stretch; }
  .nx-brief { font-size: 16px; }
  .nx-accounts thead { display: none; }
  .nx-accounts, .nx-accounts tbody { display: block; }
  .nx-accounts tbody tr:not(.nx-grp) { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 4px 12px; padding: 14px 0; border-bottom: 1px solid var(--line); }
  .nx-accounts .nx-grp { display: block; }
  .nx-accounts .nx-grp td { display: block; padding: 22px 0 8px; }
  .nx-accounts tbody tr:not(.nx-grp) td { padding: 0; border: 0; font-size: 15px; }
  .nx-accounts td.c-name { grid-column: 1; grid-row: 1; }
  .nx-accounts td.c-mtd { grid-column: 2; grid-row: 1; font-size: 17px !important; font-weight: 500; }
  .nx-accounts td.c-nav { grid-column: 1; grid-row: 2; text-align: left; font-size: 14px !important; color: var(--muted-strong); }
  .nx-accounts td.c-ytd { grid-column: 2; grid-row: 2; font-size: 13px !important; }
  .nx-accounts td.c-mtd::after { display: block; content: "Month to date"; color: var(--muted); font-size: 9.5px; font-weight: 600; letter-spacing: .14em; text-transform: uppercase; }
  .nx-accounts td.c-ytd::before { content: "YTD "; color: var(--muted); font-size: 9.5px; font-weight: 600; letter-spacing: .14em; }
  .nx-accounts td.c-spark { display: none; }
  .nx-accounts td.c-state { grid-column: 1 / -1; grid-row: 3; text-align: left; }
  .nx-acct a b { font-size: 16px; }
}
@media print { .nx-rail, .nx-top, .nx-tabbar, .nx-pop { display: none !important; } html[data-design="next"] body { padding: 0; } }
"""

NEXT_SCRIPT = r"""
(()=>{
const C=window.WR_NEXT||{},d=document,root=d.documentElement;
const $=(s,r=d)=>r.querySelector(s),$$=(s,r=d)=>[...r.querySelectorAll(s)];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const store={get(k,f){try{const v=localStorage.getItem(k);return v==null?f:JSON.parse(v)}catch(e){return f}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const fmtDate=v=>{const m=/^(\d{4})-(\d{2})-(\d{2})/.exec(String(v||''));if(!m)return v||'—';return new Date(Date.UTC(+m[1],m[2]-1,+m[3])).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'})};
const isoText=s=>String(s).replace(/\b\d{4}-\d{2}-\d{2}\b/g,fmtDate);
const minus=t=>String(t).replace(/^-/,'−');
const pct=n=>n==null||!Number.isFinite(Number(n))?'—':(Number(n)>=5e-5?'+':'')+minus(new Intl.NumberFormat('en-US',{style:'percent',minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n))).replace(/^−(0\.00%)$/,'$1');
const tone=n=>n==null||!Number.isFinite(Number(n))||Math.abs(Number(n))<5e-5?'':Number(n)>0?'gain':'loss';
const money=(n,c)=>n==null||!Number.isFinite(Number(n))?'—':minus(new Intl.NumberFormat('en-US',{maximumFractionDigits:0}).format(Number(n)))+(c?' '+c:'');
const ICON={
 home:'<path d="M4 11l8-7 8 7v9h-5v-6h-6v6H4z"/>',overview:'<rect x="4" y="4" width="7" height="7"/><rect x="13" y="4" width="7" height="7"/><rect x="4" y="13" width="7" height="7"/><rect x="13" y="13" width="7" height="7"/>',
 performance:'<path d="M4 19h16M5 15l4-5 4 3 6-7"/>',holdings:'<circle cx="12" cy="12" r="8"/><path d="M12 4v8l6 5"/>',risk:'<path d="M12 3l8 3v6c0 5-4 8-8 9-4-1-8-4-8-9V6z"/>',
 cash:'<rect x="3" y="6" width="18" height="12"/><circle cx="12" cy="12" r="3"/>',trades:'<path d="M5 8h13l-3-3M19 16H6l3 3"/>',modelling:'<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
 reports:'<path d="M6 3h9l4 4v14H6z"/><path d="M9 12h7M9 16h7"/>',exports:'<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>','ai-analyst':'<path d="M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z"/>',
 'combined-holdings':'<path d="M12 4l9 5-9 5-9-5z"/><path d="M3 14l9 5 9-5"/>','agent-inbox':'<path d="M4 13l3-8h10l3 8v6H4z"/><path d="M4 13h5l1 2h4l1-2h5"/>','metrics-guide':'<path d="M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3z"/><path d="M5 17a3 3 0 013-3h11"/>',
 'system-health':'<path d="M3 12h4l2-5 4 10 2-5h6"/>',connections:'<path d="M9 7V3M15 7V3M7 7h10v4a5 5 0 01-10 0zM12 16v5"/>','account-onboarding':'<circle cx="10" cy="8" r="4"/><path d="M3 20c0-4 3-6 7-6M17 13v6M14 16h6"/>',
 'month-end':'<rect x="4" y="5" width="16" height="15"/><path d="M4 10h16M9 3v4M15 3v4"/>',administration:'<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>',
 search:'<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>',more:'<path d="M4 7h16M4 12h16M4 17h16"/>',back:'<path d="M15 5l-7 7 7 7"/>',pin:'<path d="M6 7l5 5-5 5M12 7l5 5-5 5"/>'};
const icon=k=>`<svg viewBox="0 0 24 24" aria-hidden="true">${ICON[k]||ICON.overview}</svg>`;
const keyOf=href=>{const p=String(href||'').split(/[?#]/)[0];if(p==='/')return 'home';if(/connections$/.test(p))return 'connections';return p.split('/').filter(Boolean).pop()||'home'};

// What the classic page already knows: its links, the signed-in person and the account.
const header=$('body > header');
const logo=header&&$('img.company-logo',header);
const linkData=a=>({label:a.textContent.replace(/\s+\d+\s*$/,'').trim(),href:a.getAttribute('href'),active:a.classList.contains('active')||a.getAttribute('aria-current')==='page',inbox:a.hasAttribute('data-agent-inbox')});
const platform=header?$$('.platform-nav > a.platform-nav-link',header).map(linkData):[];
const operations=header?$$('.platform-menu-link',header).map(linkData):[];
const session=header&&$('.session',header);
const sessionParts=session?session.textContent.split('·').map(x=>x.trim()):[];
const who={email:sessionParts[0]||'',role:sessionParts[1]||'',logout:(session&&$('a[href*="logout"]',session)||{getAttribute:()=>'/.auth/logout'}).getAttribute('href')};
const path=location.pathname,match=path.match(/^\/accounts\/([^/]+)\/([^/]+)(?:\/([^/]+))?/),accountNav=$('.account-nav');
const cleanName=(id,name)=>{const n=String(name||'').trim(),e=id.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');const p=n.match(new RegExp('^'+e+'\\s*\\((.*)\\)$'));if(p)return p[1].trim();return n.replace(new RegExp('^'+e+'\\s*[-–—]\\s*'),'').trim()||n};
let accounts=store.get('wr-next-accounts',[]);
const switcher=$('#account-switcher');
if(switcher&&switcher.options.length)accounts=[...switcher.options].map(o=>({id:o.value,name:cleanName(o.value,o.textContent)}));
const homeCards=$$('main > .accounts .account-card');
if(homeCards.length)accounts=homeCards.map(card=>{const stats=$('[data-account-stats]',card),id=stats?stats.dataset.accountStats:'';return{id,name:cleanName(id,($('.account-id',card)||{}).textContent),currency:(($('.account-badge',card)||{}).textContent||'').split(' ')[0],since:(($('.account-since',card)||{}).textContent||'').replace(/^Data from\s*/,'')}}).filter(a=>a.id);
if(accounts.length)store.set('wr-next-accounts',accounts.map(({id,name,currency})=>({id,name,currency})));
const account=match&&accountNav?{id:decodeURIComponent(match[1]),section:match[2],part:match[3]||null}:null;
if(account){const known=accounts.find(a=>a.id===account.id),h1=$('.account-hero h1');account.name=known?known.name:cleanName(account.id,h1?h1.textContent:account.id)}
const sections=account?$$(':scope > a.account-nav-link, :scope > .account-nav-item',accountNav).map(node=>{const a=node.matches('a')?node:$('a.account-nav-link',node);return{key:keyOf(a.getAttribute('href')),label:a.textContent.replace('▾','').trim(),href:a.getAttribute('href'),active:a.classList.contains('active'),parts:$$('.account-nav-menu a',node).map(p=>({label:p.textContent.trim(),href:p.getAttribute('href'),current:p.getAttribute('aria-current')==='page'}))}}):[];
const pins=new Set(store.get('wr-next-pins',[]));

// The rail.
const navLink=(item,key,extra='')=>`<a class="nx-link" href="${esc(item.href)}"${item.active?' aria-current="page"':''}${item.inbox?' data-agent-inbox':''}${extra}>${icon(key||keyOf(item.href))}<span>${esc(item.label)}</span></a>`;
const ACCOUNT_GROUPS=[[null,['overview','performance','holdings','risk']],['Activity',['cash','trades']],['More',['modelling','reports','exports','ai-analyst']]];
const railNav=()=>{
  let html='';
  if(account){
    html+=`<a class="nx-link" href="/">${icon('back')}<span>All accounts</span></a>`;
    const used=new Set();
    for(const [group,keys] of ACCOUNT_GROUPS){
      const items=sections.filter(s=>keys.includes(s.key));const rest=group==='More'?sections.filter(s=>!used.has(s.key)&&!keys.includes(s.key)&&!ACCOUNT_GROUPS.some(g=>g[1].includes(s.key))):[];
      const all=[...items,...rest];if(!all.length)continue;all.forEach(s=>used.add(s.key));
      if(group)html+=`<div class="nx-group">${esc(group)}</div>`;
      for(const s of all){html+=navLink({...s,active:s.active},s.key);if(s.active&&s.parts.length)html+='<div class="nx-sub">'+s.parts.map(p=>`<a href="${esc(p.href)}"${p.current?' aria-current="page"':''}>${esc(p.label)}</a>`).join('')+'</div>'}
    }
    html+='<div class="nx-group">Platform</div>';
    html+=platform.filter(p=>keyOf(p.href)!=='home').map(p=>navLink(p)).join('');
  }else{
    html+=platform.map(p=>navLink(p,keyOf(p.href)==='home'?'home':null)).join('');
    const pinned=accounts.filter(a=>pins.has(a.id));
    if(pinned.length)html+='<div class="nx-group">Pinned</div>'+pinned.map(a=>navLink({label:a.name,href:`/accounts/${encodeURIComponent(a.id)}/overview`},'overview')).join('');
  }
  if(operations.length)html+='<div class="nx-group">Operations</div>'+operations.map(p=>navLink(p)).join('');
  return `<nav class="nx-nav" aria-label="Main">${html}</nav>`;
};
const switchButton=()=>account?`<button class="nx-switch" type="button" data-nx-switch aria-haspopup="dialog"><small>Account</small><b>${esc(account.name)}</b></button>`:'';
const rail=d.createElement('aside');rail.className='nx-rail';rail.setAttribute('aria-label','Site');
rail.innerHTML=`<a class="nx-brand" href="/" aria-label="WealthRyse accounts home">${logo?`<img src="${logo.getAttribute('src')}" alt="">`:''}<span>WealthRyse</span></a>${switchButton()}${railNav()}<button class="nx-rail-pin" type="button" data-nx-pin aria-pressed="false">${icon('pin')}<span>Keep menu open</span><kbd>[</kbd></button><div class="nx-rail-foot">${esc(who.email)}${who.role?' · '+esc(who.role):''}</div>`;
// The rail stays a slim strip of icons unless pinned. Pointing at it or tabbing into it opens it over the page.
const railPin=$('[data-nx-pin]',rail);
const setPinned=on=>{d.documentElement.classList.toggle('nx-pinned',on);rail.classList.remove('is-peek');railPin.setAttribute('aria-pressed',String(on));
  railPin.title=on?'Collapse the menu ([)':'Keep the menu open ([)';$('span',railPin).textContent=on?'Collapse menu':'Keep menu open';store.set('wr-next-rail',on?'pinned':'mini')};
setPinned(store.get('wr-next-rail','mini')==='pinned');
railPin.addEventListener('click',()=>setPinned(!d.documentElement.classList.contains('nx-pinned')));
let peekTimer;
const peek=on=>{clearTimeout(peekTimer);if(d.documentElement.classList.contains('nx-pinned'))return;peekTimer=setTimeout(()=>rail.classList.toggle('is-peek',on),on?140:220)};
rail.addEventListener('mouseenter',()=>peek(true));rail.addEventListener('mouseleave',()=>peek(false));
rail.addEventListener('focusin',()=>{clearTimeout(peekTimer);if(!d.documentElement.classList.contains('nx-pinned'))rail.classList.add('is-peek')});
rail.addEventListener('focusout',e=>{if(!rail.contains(e.relatedTarget))rail.classList.remove('is-peek')});
rail.addEventListener('keydown',e=>{if(e.key==='Escape'&&rail.classList.contains('is-peek')){rail.classList.remove('is-peek');d.activeElement.blur()}});
$$('.nx-link',rail).forEach(a=>{if(!a.title)a.title=a.textContent.trim()});

// The top bar.
const top=d.createElement('div');top.className='nx-top';
const mac=/Mac|iPhone|iPad/.test(navigator.platform||navigator.userAgent);
const toggle=header&&$('[data-theme-toggle]',header);
top.innerHTML=`<a class="nx-brand" href="/" aria-label="WealthRyse accounts home">${logo?`<img src="${logo.getAttribute('src')}" alt="">`:''}<span>WealthRyse</span></a>${switchButton()}
<button class="nx-search" type="button" data-nx-search aria-label="Search or ask (${mac?'⌘':'Ctrl+'}K)">${icon('search')}<span class="nx-search-text">${account?'Search or ask about this account':'Search accounts, holdings, metrics or ask'}…</span><kbd>${mac?'⌘':'Ctrl'} K</kbd></button>
<button class="nx-status" type="button" data-nx-status hidden></button><span class="nx-spacer"></span>
<div class="nx-user"><button type="button" aria-haspopup="menu" aria-expanded="false" aria-label="Your menu">${esc((who.email||'?').charAt(0))}</button><div class="nx-menu" role="menu" hidden>
<div class="nx-menu-who"><b>${esc(who.email)}</b>${esc(who.role)}</div>
${toggle?`<button type="button" role="menuitem" data-nx-theme>${esc(toggle.textContent.trim())}</button>`:''}
${C.canClassic?`<a role="menuitem" href="/design/classic?back=${encodeURIComponent(location.pathname+location.search)}">Use the classic design</a>`:''}
<a role="menuitem" href="${esc(who.logout)}">Sign out</a></div></div>`;
d.body.prepend(rail,top);
const userBox=$('.nx-user',top),userButton=$('button',userBox),menu=$('.nx-menu',userBox);
const closeMenu=()=>{menu.hidden=true;userButton.setAttribute('aria-expanded','false')};
userButton.addEventListener('click',e=>{e.stopPropagation();menu.hidden=!menu.hidden;userButton.setAttribute('aria-expanded',String(!menu.hidden))});
d.addEventListener('click',e=>{if(!userBox.contains(e.target))closeMenu()});
const themeButton=$('[data-nx-theme]',top);
if(themeButton)themeButton.addEventListener('click',()=>{toggle.click();themeButton.textContent=toggle.textContent.trim();closeMenu()});

// Phone tab bar.
const tabs=d.createElement('nav');tabs.className='nx-tabbar';tabs.setAttribute('aria-label','Sections');
const tab=(label,href,key,current)=>`<a href="${esc(href)}"${current?' aria-current="page"':''}>${icon(key)}<span>${esc(label)}</span></a>`;
if(account){tabs.innerHTML=sections.filter(s=>['overview','performance','holdings','risk'].includes(s.key)).slice(0,4).map(s=>tab(s.label,s.href,s.key,s.active)).join('')}
else{const pick=k=>platform.find(p=>keyOf(p.href)===k);tabs.innerHTML=[['Accounts','home'],['Holdings','combined-holdings']].map(([l,k])=>{const p=pick(k);return p?tab(l,p.href,k,p.active):''}).join('')+`<button type="button" data-nx-search>${icon('search')}<span>Search</span></button>`+(pick('agent-inbox')?tab('Inbox',pick('agent-inbox').href,'agent-inbox',pick('agent-inbox').active):(pick('metrics-guide')?tab('Guide',pick('metrics-guide').href,'metrics-guide',pick('metrics-guide').active):''))}
tabs.insertAdjacentHTML('beforeend',`<button type="button" data-nx-more>${icon('more')}<span>More</span></button>`);
d.body.appendChild(tabs);

// Sheets: account switcher and More.
const sheet=(title,body)=>{const dlg=d.createElement('dialog');dlg.className='nx-sheet';dlg.innerHTML=`<div class="nx-sheet-head"><span>${esc(title)}</span><button type="button" aria-label="Close">×</button></div>${body}`;$('.nx-sheet-head button',dlg).addEventListener('click',()=>dlg.close());dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close()});d.body.appendChild(dlg);dlg.addEventListener('close',()=>dlg.remove());dlg.showModal();return dlg};
const switchSheet=()=>sheet('Switch account','<nav class="nx-nav">'+accounts.map(a=>navLink({label:a.name,href:`/accounts/${encodeURIComponent(a.id)}/${account?account.section:'overview'}`,active:account&&a.id===account.id},'overview')).join('')+'</nav>');
$$('[data-nx-switch]').forEach(b=>b.addEventListener('click',switchSheet));
$$('[data-nx-more]').forEach(b=>b.addEventListener('click',()=>sheet(account?account.name:'Menu',railNav()+`<div class="nx-rail-foot">${esc(who.email)}</div>`)));

// Status chip: the as-of date and whether the figures are certified.
const status=$('[data-nx-status]',top);
const freshness=$('#freshness');
const updateStatus=()=>{if(!freshness||!freshness.textContent.trim())return;
  const text=freshness.textContent.replace(/^Data as of\s*/i,'').split('·')[0].trim(),cert=$('.certified-line')?' · Certified':'';
  status.textContent='As of '+text+cert;status.className='nx-status '+(freshness.classList.contains('stale')?'stale':freshness.classList.contains('late')?'late':'');status.hidden=false};
if(freshness){new MutationObserver(updateStatus).observe(freshness,{attributes:true,childList:true,subtree:true});const grid=$('#portfolio-grid');if(grid)new MutationObserver(()=>{clearTimeout(updateStatus.t);updateStatus.t=setTimeout(updateStatus,150)}).observe(grid,{childList:true,subtree:true});updateStatus();freshness.hidden=true;new MutationObserver(()=>{if(!freshness.hidden)freshness.hidden=true}).observe(freshness,{attributes:true,attributeFilter:['hidden']})}
status.addEventListener('click',()=>{const s=$('#asof-select');if(s){s.scrollIntoView({block:'center'});s.focus()}});

// The open section's pages as tabs above the content.
if(account){const open=sections.find(s=>s.active);const sticky=$('.account-sticky');
  if(open&&open.parts.length>1&&sticky)sticky.insertAdjacentHTML('afterbegin',`<nav class="nx-subtabs" aria-label="${esc(open.label)} pages">${open.parts.map(p=>`<a href="${esc(p.href)}"${p.current?' aria-current="page"':''}>${esc(p.label)}</a>`).join('')}</nav>`)}

// Account header: the name leads, then the IBKR number, currency and start date.
if(account){const h1=$('.account-hero h1'),sub=$('.account-hero-copy > .muted');if(h1)h1.textContent=account.name;
  if(sub)sub.innerHTML=(account.name!==account.id?`<span class="nx-id">${esc(account.id)}</span> · `:'')+esc(isoText(sub.textContent))}

// Keep dates in one format where the page writes them as ISO dates.
const tidyDates=sel=>{const node=$(sel);if(!node)return;const fix=()=>{const t=isoText(node.textContent);if(t!==node.textContent&&!node.children.length)node.textContent=t};new MutationObserver(fix).observe(node,{childList:true,characterData:true,subtree:true});fix()};
['#overview-asof','#risk-asof','#holdings-asof','#cash-asof','#sector-summary'].forEach(tidyDates);
const tidyNotes=()=>$$('#portfolio-grid .metric .muted').forEach(n=>{if(!n.children.length){const t=isoText(n.textContent);if(t!==n.textContent)n.textContent=t}});

// Overview: one sentence that restates the figures the page has loaded.
const overview=account&&account.section==='overview'?$('.page-view[data-section="overview"]'):null;
if(overview){const brief=d.createElement('p');brief.className='nx-brief';brief.setAttribute('aria-live','polite');brief.hidden=true;const navCard=$('.overview-nav',overview);(navCard||overview.firstChild).after(brief);
  const tiles=()=>Object.fromEntries($$('#overview-returns .metric').map(m=>[(($('.label',m)||{}).textContent||'').replace('↗','').trim().toLowerCase(),(($('.value',m)||{}).textContent||'').trim()]));
  const signed=(v,word)=>{if(!v||v==='—')return '';const neg=/^[−-]/.test(v);return `${word?(neg?'down':'up')+' ':''}<b class="${neg?'loss':'gain'}">${esc(v.replace(/^[+−-]/,''))}</b>`};
  const write=()=>{const t=tiles(),m=t['month to date'],y=t['year to date'];if(!m&&!y)return;const parts=[];
    if(m&&m!=='—')parts.push(`${/^[−-]/.test(m)?'Down':'Up'} ${signed(m)} this month`);
    if(y&&y!=='—')parts.push(`${parts.length?'and ':''}${parts.length?signed(y,true):(/^[−-]/.test(y)?'Down ':'Up ')+signed(y)} this year`);
    let s=parts.length?parts.join(' ')+'.':'';
    const h=$('#overview-holdings .sector-row'),sec=$('#overview-sectors .sector-row');
    if(h){const sym=(($('strong',h)||{}).textContent||'').trim(),w=(($('.sector-weight',h)||{}).textContent||'').trim();if(sym&&w)s+=` The largest holding is <b>${esc(sym)}</b> at <b>${esc(w)}</b>`;
      if(sec){const n=(($('.sector-name',sec)||{}).textContent||'').trim(),sw=(($('.sector-weight',sec)||{}).textContent||'').trim();if(n&&sw)s+=`, and <b>${esc(n)}</b> is the largest sector at <b>${esc(sw)}</b>`}s+=sym&&w?'.':''}
    s=s.trim();if(brief.innerHTML!==s)brief.innerHTML=s;brief.hidden=!s};
  new MutationObserver(()=>{clearTimeout(write.t);write.t=setTimeout(write,120)}).observe(overview,{childList:true,subtree:true});}

// Figure details.
let pop=null;const closePop=()=>{if(pop){pop.remove();pop=null}};
const guideFor=label=>{const m=C.metrics||{},k=Object.keys(m).find(x=>x.toLowerCase()===label.toLowerCase());return k?'/metrics-guide#'+m[k]:null};
const showPop=(target,e)=>{
  e.preventDefault();e.stopPropagation();if(pop&&pop.dataset.for===target.dataset.nxInfo){closePop();return}closePop();
  const card=target.closest('.card'),cert=card&&$(':scope > .certified-line',card),asOf=$('#asof-select');
  const label=target.id==='nav'?'Net asset value':(($('.label',target)||{}).textContent||'').replace('↗','').trim();
  const value=target.id==='nav'?target.textContent.trim():(($('.value',target)||{}).textContent||'').trim();
  const note=target.id==='nav'?'':(($('.muted',target)||{}).textContent||'').trim();
  const link=(target.matches('a[href]')&&target.getAttribute('href'))||guideFor(label)||(cert&&$('a',cert)&&$('a',cert).getAttribute('href'))||'/metrics-guide';
  const covers=asOf&&asOf.selectedOptions[0]?asOf.selectedOptions[0].textContent:'';
  const status=cert?esc(cert.textContent.replace(/·\s*how this is worked out\s*$/i,'').trim()):'Calculated from the daily account data';
  pop=d.createElement('div');pop.className='nx-pop';pop.setAttribute('role','dialog');pop.setAttribute('aria-label',label);pop.dataset.for=target.dataset.nxInfo;
  pop.innerHTML=`<h4>${esc(label)}</h4><div class="nx-pop-value">${esc(value)}</div><dl>${covers?`<dt>Covers</dt><dd>${esc(covers)}</dd>`:''}${note?`<dt>Basis</dt><dd>${esc(note)}</dd>`:''}<dt>Status</dt><dd>${status}</dd></dl><a href="${esc(link)}">How this is worked out →</a>`;
  d.body.appendChild(pop);const r=target.getBoundingClientRect(),w=pop.offsetWidth;
  pop.style.left=Math.max(16,Math.min(window.scrollX+r.left,window.scrollX+d.documentElement.clientWidth-w-16))+'px';pop.style.top=(window.scrollY+r.bottom+8)+'px';$('a',pop).focus({preventScroll:true})};
let infoSeq=0;
const markInfo=()=>{$$('#portfolio-grid .metric:not([data-nx-info]), #nav:not([data-nx-info])').forEach(node=>{if(!$('.value',node)&&node.id!=='nav')return;node.dataset.nxInfo=String(++infoSeq);node.setAttribute('aria-haspopup','dialog');if(!node.matches('a,button')){node.tabIndex=0;node.setAttribute('role','button')}})};
const grid=$('#portfolio-grid');if(grid){markInfo();new MutationObserver(()=>{clearTimeout(markInfo.t);markInfo.t=setTimeout(()=>{markInfo();tidyNotes()},100)}).observe(grid,{childList:true,subtree:true});
  grid.addEventListener('click',e=>{const t=e.target.closest('[data-nx-info]');if(t)showPop(t,e)});
  grid.addEventListener('keydown',e=>{const t=e.target.closest('[data-nx-info]');if(t&&!t.matches('a,button')&&(e.key==='Enter'||e.key===' '))showPop(t,e)})}
d.addEventListener('click',e=>{if(pop&&!pop.contains(e.target))closePop()});
d.addEventListener('keydown',e=>{if(e.key==='Escape'&&pop){const id=pop.dataset.for;closePop();const t=$(`[data-nx-info="${id}"]`);if(t)t.focus()}});

// Prefill a table filter from ?q= (used by search results).
const q=new URLSearchParams(location.search).get('q');
if(q){const f=$('#holdings-filter')||$('#combined-filter');if(f){const fill=()=>{f.value=q;f.dispatchEvent(new Event('input',{bubbles:true}))};fill();setTimeout(fill,1200)}}
const pendingAsk=sessionStorage.getItem('wr-next-ask');
if(pendingAsk){sessionStorage.removeItem('wr-next-ask');const box=$('#analysis-question');if(box){box.value=pendingAsk;box.focus()}}

// Search or ask.
let holdingsIndex=null;
const loadHoldings=()=>holdingsIndex||(holdingsIndex=fetch('/api/holdings/combined?currency=USD',{credentials:'same-origin'}).then(r=>r.ok?r.json():{holdings:[]}).then(x=>x.holdings||[]).catch(()=>[]));
const baseItems=()=>{const items=[];
  accounts.forEach(a=>items.push({group:'Accounts',label:a.name,hint:a.currency?a.currency+' · '+a.id:a.id,href:`/accounts/${encodeURIComponent(a.id)}/overview`}));
  if(account)sections.forEach(s=>{items.push({group:'This account',label:s.label,hint:account.name,href:s.href});s.parts.forEach(p=>items.push({group:'This account',label:`${s.label} › ${p.label}`,hint:account.name,href:p.href}))});
  [...platform,...operations].forEach(p=>items.push({group:'Pages',label:p.label,hint:'Page',href:p.href}));
  Object.entries(C.metrics||{}).forEach(([label,anchor])=>items.push({group:'Metric definitions',label,hint:'Metrics guide',href:'/metrics-guide#'+anchor}));
  return items};
const score=(text,query)=>{const t=text.toLowerCase(),words=query.toLowerCase().split(/\s+/).filter(Boolean);let s=0;for(const w of words){const i=t.indexOf(w);if(i<0)return 0;s+=i===0?6:/[\s›(·-]/.test(t[i-1])?4:1}return s};
const mark=(text,query)=>{let out=esc(text);query.toLowerCase().split(/\s+/).filter(w=>w.length>1).forEach(w=>{out=out.replace(new RegExp('('+w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+')','i'),'<mark>$1</mark>')});return out};
const canAsk=()=>!!($('.wr-fab')||(account&&sections.some(s=>s.key==='ai-analyst')));
const ask=question=>{const fab=$('.wr-fab');if(fab){if(fab.getAttribute('aria-expanded')!=='true')fab.click();setTimeout(()=>{const t=$('.wr-form textarea');if(t){t.value=question;t.focus()}},60);return}
  const s=sections.find(x=>x.key==='ai-analyst');if(s){sessionStorage.setItem('wr-next-ask',question);location.href=s.href}};
const openPalette=()=>{if($('.nx-palette'))return;closePop();closeMenu();
  const dlg=d.createElement('dialog');dlg.className='nx-palette';dlg.setAttribute('aria-label','Search or ask');
  dlg.innerHTML=`<form method="dialog">${icon('search').replace('<svg','<svg width="18" height="18" style="stroke:currentColor;fill:none;stroke-width:1.8;flex:none"')}<input type="text" id="nx-query" autocomplete="off" spellcheck="false" placeholder="${account?'Pages, holdings, metrics, or a question about '+esc(account.name):'Accounts, holdings, pages, metrics, or a question'}" aria-label="Search or ask" role="combobox" aria-expanded="true" aria-controls="nx-results" aria-autocomplete="list"><kbd>Esc</kbd></form><div class="nx-results" id="nx-results" role="listbox"></div><div class="nx-palette-foot"><span>↑ ↓ move</span><span>↵ open</span>${canAsk()?'<span>Tab ask the analyst</span>':''}</div>`;
  d.body.appendChild(dlg);dlg.showModal();
  const input=$('input',dlg),list=$('.nx-results',dlg);let shown=[],sel=0;const recent=store.get('wr-next-recent',[]);
  const go=item=>{if(!item)return;if(item.ask){dlg.close();ask(item.ask);return}store.set('wr-next-recent',[{label:item.label,hint:item.hint,href:item.href,group:'Recent'},...recent.filter(r=>r.href!==item.href)].slice(0,5));location.href=item.href};
  const render=async()=>{const query=input.value.trim();let items;
    if(!query)items=[...recent.map(r=>({...r,group:'Recent'})),...baseItems().filter(i=>i.group==='Accounts'||i.group==='This account').slice(0,10)];
    else{items=baseItems().map(i=>({...i,s:score(i.label+' '+(i.hint||''),query)})).filter(i=>i.s).sort((a,b)=>b.s-a.s);
      if(query.length>=2){const hs=await loadHoldings();if(input.value.trim()!==query)return;
        hs.map(h=>({h,s:score(`${h.symbol||''} ${h.description||''}`,query)})).filter(x=>x.s).sort((a,b)=>b.s-a.s).slice(0,4).forEach(({h,s})=>{
          items.push({group:'Holdings',label:`${h.symbol||h.conid} · ${h.description||''}`,hint:`In ${h.account_count} account${h.account_count===1?'':'s'}`,href:`/combined-holdings?q=${encodeURIComponent(h.symbol||'')}`,s:s+3});
          (h.accounts||[]).slice(0,3).forEach(a=>items.push({group:'Holdings',label:`${h.symbol} in ${cleanName(a.account_id,a.display_name)}`,hint:'Holdings',href:`/accounts/${encodeURIComponent(a.account_id)}/holdings?q=${encodeURIComponent(h.symbol||'')}`,s}))})}
      if(canAsk())items.push({group:'Ask the analyst',label:`“${query}”`,hint:account?account.name:'Portfolio analyst',ask:query})}
    const groups=[];items.forEach(i=>{let g=groups.find(x=>x.name===i.group);if(!g)groups.push(g={name:i.group,items:[]});if(g.items.length<8)g.items.push(i)});
    shown=groups.flatMap(g=>g.items);sel=Math.min(sel,Math.max(shown.length-1,0));let n=0;
    list.innerHTML=groups.map(g=>`<h5>${esc(g.name)}</h5>`+g.items.map(i=>`<a class="nx-result" role="option" id="nx-r${n}" data-i="${n}" aria-selected="${n++===sel}" ${i.href?`href="${esc(i.href)}"`:'href="#"'}><span>${mark(i.label,query)}</span><small>${esc(i.hint||'')}</small></a>`).join('')).join('')||`<div class="nx-empty" style="padding:18px">Nothing matches “${esc(query)}”.</div>`;
    input.setAttribute('aria-activedescendant','nx-r'+sel)};
  const move=k=>{sel=(sel+k+shown.length)%Math.max(shown.length,1);$$('.nx-result',list).forEach(a=>a.setAttribute('aria-selected',String(+a.dataset.i===sel)));const a=$(`#nx-r${sel}`,list);if(a)a.scrollIntoView({block:'nearest'});input.setAttribute('aria-activedescendant','nx-r'+sel)};
  input.addEventListener('input',()=>{sel=0;render()});
  input.addEventListener('keydown',e=>{if(e.key==='ArrowDown'){e.preventDefault();move(1)}else if(e.key==='ArrowUp'){e.preventDefault();move(-1)}else if(e.key==='Enter'){e.preventDefault();go(shown[sel])}else if(e.key==='Tab'&&input.value.trim()&&canAsk()){e.preventDefault();dlg.close();ask(input.value.trim())}});
  list.addEventListener('click',e=>{const a=e.target.closest('.nx-result');if(a){e.preventDefault();go(shown[+a.dataset.i])}});
  dlg.addEventListener('click',e=>{if(e.target===dlg)dlg.close()});dlg.addEventListener('close',()=>dlg.remove());render()};
$$('[data-nx-search]').forEach(b=>b.addEventListener('click',openPalette));
d.addEventListener('keydown',e=>{const typing=e.target.closest&&e.target.closest('input,textarea,select,[contenteditable="true"]');
  if((e.key==='k'||e.key==='K')&&(e.metaKey||e.ctrlKey)){e.preventDefault();openPalette()}else if(e.key==='/'&&!typing&&!e.metaKey&&!e.ctrlKey){e.preventDefault();openPalette()}
  else if(e.key==='['&&!typing&&!e.metaKey&&!e.ctrlKey&&!e.altKey&&railPin.offsetParent){e.preventDefault();setPinned(!d.documentElement.classList.contains('nx-pinned'))}});

// Home briefing.
const accountsGrid=$('main > .accounts');
if(path==='/'&&accountsGrid){
  const greet=(h=>h<12?'Good morning':h<18?'Good afternoon':'Good evening')(new Date().getHours());
  const section=d.createElement('section');section.className='nx-home';
  section.innerHTML=`<div class="nx-home-head"><div class="nx-eyebrow">${esc(greet)}</div><h1>Your accounts</h1></div><p class="nx-brief" aria-live="polite"><span class="nx-skel"></span></p>
<div class="nx-table-wrap"><table class="nx-accounts"><thead><tr><th scope="col" aria-sort="none"><button type="button" data-sort="group">Account</button></th><th scope="col" aria-sort="none"><button type="button" data-sort="nav">Net asset value</button></th><th scope="col" aria-sort="none"><button type="button" data-sort="mtd">Month to date</button></th><th scope="col" aria-sort="none"><button type="button" data-sort="ytd">Year to date</button></th><th scope="col">Last 3 months</th><th scope="col">Data</th></tr></thead><tbody></tbody></table></div>`;
  accountsGrid.before(section);
  const tbody=$('tbody',section),brief=$('.nx-brief',section);
  if(!accounts.length){const empty=$('.empty',accountsGrid);tbody.innerHTML=`<tr><td colspan="6" class="nx-empty">${esc(empty?empty.textContent:'No accounts are available.')}</td></tr>`;brief.hidden=true}
  let rows=accounts.map((a,i)=>({...a,order:i,nav:null,mtd:null,ytd:null,nav_date:null,certified:null,loaded:false})),sortKey='group',sortDir=-1;
  const sparks=new Map();
  const nameCell=r=>`<div class="nx-acct"><button class="nx-pin" type="button" data-pin="${esc(r.id)}" aria-pressed="${pins.has(r.id)}" aria-label="${pins.has(r.id)?'Unpin':'Pin'} ${esc(r.name)}">${pins.has(r.id)?'★':'☆'}</button><a href="/accounts/${encodeURIComponent(r.id)}/overview"><b>${esc(r.name)}</b><small>${esc(r.id)}${r.since?' · since '+esc(/^\d{4}-\d{2}-\d{2}$/.test(r.since)?fmtDate(r.since):r.since.replace(/\b(January|February|March|April|June|July|August|September|October|November|December)\b/,m=>m.slice(0,3))):''}</small></a></div>`;
  const latestDate=()=>rows.map(r=>r.nav_date).filter(Boolean).sort().pop();
  const stateCell=r=>{if(!r.loaded)return '<span class="nx-skel" style="width:60px"></span>';if(!r.nav_date)return '<span class="nx-state">No data yet</span>';const behind=r.nav_date<latestDate();return `<span class="nx-state ${behind?'behind':r.certified?'cert':''}">${behind?'As of '+esc(fmtDate(r.nav_date)):r.certified?esc(r.certified.note||'Certified'):'As of '+esc(fmtDate(r.nav_date))}</span>`};
  const row=r=>`<tr data-id="${esc(r.id)}"><td class="c-name">${nameCell(r)}</td><td class="c-nav nx-nav-cell">${r.loaded?esc(money(r.nav,r.currency)):'<span class="nx-skel"></span>'}</td><td class="c-mtd ${tone(r.mtd)}">${r.loaded?esc(pct(r.mtd)):'<span class="nx-skel" style="width:50px"></span>'}</td><td class="c-ytd ${tone(r.ytd)}">${r.loaded?esc(pct(r.ytd)):'<span class="nx-skel" style="width:50px"></span>'}</td><td class="c-spark">${sparks.get(r.id)||'<svg class="nx-spark" aria-hidden="true"></svg>'}</td><td class="c-state">${stateCell(r)}</td></tr>`;
  const render=()=>{let list=[...rows],html='';
    if(sortKey==='group'){const groups=[];list.sort((a,b)=>(pins.has(b.id)-pins.has(a.id))||a.order-b.order).forEach(r=>{const k=r.currency||'Other';let g=groups.find(x=>x.k===k);if(!g)groups.push(g={k,rows:[]});g.rows.push(r)});
      html=groups.map(g=>(groups.length>1?`<tr class="nx-grp"><td colspan="6">${esc(g.k)} reporting</td></tr>`:'')+g.rows.map(row).join('')).join('')}
    else{list.sort((a,b)=>{const x=a[sortKey],y=b[sortKey];if(x==null&&y==null)return a.order-b.order;if(x==null)return 1;if(y==null)return -1;return sortDir*(y-x)});html=list.map(row).join('')}
    tbody.innerHTML=html;$$('th[aria-sort]',section).forEach(th=>{const k=$('button',th).dataset.sort;th.setAttribute('aria-sort',k===sortKey&&k!=='group'?(sortDir<0?'descending':'ascending'):'none')})};
  if(accounts.length)render();
  $('thead',section).addEventListener('click',e=>{const b=e.target.closest('button[data-sort]');if(!b)return;const k=b.dataset.sort;if(k===sortKey&&k!=='group')sortDir=-sortDir;else{sortKey=k;sortDir=-1}render()});
  tbody.addEventListener('click',e=>{const b=e.target.closest('[data-pin]');if(!b)return;e.preventDefault();const id=b.dataset.pin;pins.has(id)?pins.delete(id):pins.add(id);store.set('wr-next-pins',[...pins]);render();const again=$(`[data-pin="${CSS.escape(id)}"]`,tbody);if(again)again.focus()});
  const sentence=()=>{const named=rows.filter(r=>r.loaded),withM=named.filter(r=>r.mtd!=null);let s='';
    if(withM.length){const up=withM.filter(r=>r.mtd>=5e-5).length;s=up===withM.length?`All ${withM.length} accounts are up this month.`:up===0?`No account is up this month.`:`${up} of ${withM.length} accounts are up this month.`;
      if(withM.length>1){const best=withM.reduce((a,b)=>b.mtd>a.mtd?b:a),worst=withM.reduce((a,b)=>b.mtd<a.mtd?b:a);s+=` <b>${esc(best.name)}</b> leads at <b class="${tone(best.mtd)}">${esc(pct(best.mtd))}</b>`;if(worst!==best)s+=`, and <b>${esc(worst.name)}</b> ${worst.mtd<0?'is down most':'trails'} at <b class="${tone(worst.mtd)}">${esc(pct(worst.mtd))}</b>`;s+='.'}}
    const latest=latestDate();if(latest){const behind=named.filter(r=>r.nav_date&&r.nav_date<latest);s+=` Figures run to <b>${esc(fmtDate(latest))}</b>`+(behind.length?`; ${behind.map(r=>`<b>${esc(r.name)}</b>`).join(', ')} ${behind.length===1?'is':'are'} behind that date.`:'.')}
    brief.innerHTML=s.trim()||'Figures are not available yet.';
    const certified=named.filter(r=>r.certified).length;if(latest){status.textContent=`Data through ${fmtDate(latest)}`+(certified?` · ${certified} of ${named.length} certified`:'');status.className='nx-status'+(named.some(r=>r.nav_date&&r.nav_date<latest)?' late':'');status.hidden=false}};
  if(accounts.length)fetch('/api/accounts/overview',{credentials:'same-origin'}).then(r=>r.ok?r.json():Promise.reject(r.status)).then(data=>{
    (data.accounts||[]).forEach(a=>{const r=rows.find(x=>x.id===a.account_id);if(r)Object.assign(r,{nav:a.nav,mtd:a.mtd,ytd:a.ytd,nav_date:a.nav_date,certified:a.certified||null,currency:a.currency||r.currency})});
    rows.forEach(r=>r.loaded=true);render();sentence();loadSparks()}).catch(()=>{rows.forEach(r=>r.loaded=true);render();brief.textContent='The account figures could not be loaded. Refresh the page; if it keeps happening, check System health.'});
  const sparkSvg=series=>{const v=series.slice(-63).map(x=>Number(x.nav)).filter(Number.isFinite);if(v.length<2)return '';const w=120,h=30,p=2,lo=Math.min(...v),hi=Math.max(...v),x=i=>p+i*(w-2*p)/(v.length-1),y=n=>h-p-(n-lo)/((hi-lo)||1)*(h-2*p);
    const line=v.map((n,i)=>`${i?'L':'M'}${x(i).toFixed(1)},${y(n).toFixed(1)}`).join('');return `<svg class="nx-spark" viewBox="0 0 ${w} ${h}" role="img" aria-label="Net asset value, last 3 months"><path class="area" d="${line}L${w-p},${h}L${p},${h}Z"/><path class="line" d="${line}"/><circle cx="${x(v.length-1).toFixed(1)}" cy="${y(v.at(-1)).toFixed(1)}" r="2.2"/></svg>`};
  const loadSparks=async()=>{const queue=rows.filter(r=>r.nav_date).map(r=>r.id);const work=async()=>{while(queue.length){const id=queue.shift();try{const res=await fetch(`/api/portfolio/${encodeURIComponent(id)}/performance`,{credentials:'same-origin'});if(!res.ok)continue;const svg=sparkSvg((await res.json()).series||[]);if(svg){sparks.set(id,svg);const cell=$(`tr[data-id="${CSS.escape(id)}"] .c-spark`,tbody);if(cell)cell.innerHTML=svg}}catch(e){}}};await Promise.all([work(),work()])};
}
})();
"""
