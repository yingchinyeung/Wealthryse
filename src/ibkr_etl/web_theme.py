"""Shared visual system for the WealthRyse portfolio portal."""

COMMON_THEME_CSS = r"""
:root {
  color-scheme: light;
  --ink: #0b1220;
  --ink-soft: #25324a;
  --muted: #667085;
  --muted-strong: #475467;
  --line: rgba(15, 23, 42, .10);
  --line-strong: rgba(15, 23, 42, .17);
  --brand: #0878f9;
  --brand-deep: #0057d9;
  --accent: #675cff;
  --chart-portfolio: #2646a6;
  --chart-benchmark: #d97706;
  --bg: #f5f5f7;
  --surface: rgba(255, 255, 255, .82);
  --surface-soft: rgba(248, 250, 252, .88);
  --shadow-sm: 0 1px 2px rgba(15, 23, 42, .04), 0 7px 24px rgba(15, 23, 42, .05);
  --shadow-lg: 0 22px 60px rgba(15, 23, 42, .10);
  --radius-md: 18px;
  --radius-lg: 26px;
}
* { box-sizing: border-box; }
html { min-width: 320px; background: var(--bg); scroll-behavior: smooth; }
body {
  margin: 0;
  color: var(--ink);
  background:
    radial-gradient(circle at 12% -8%, rgba(8, 120, 249, .10), transparent 31rem),
    radial-gradient(circle at 94% 4%, rgba(103, 92, 255, .08), transparent 27rem),
    linear-gradient(180deg, #fbfbfd 0, var(--bg) 31rem, #f2f4f8 100%);
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", Inter, "Segoe UI", Arial, sans-serif;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
header {
  position: sticky;
  top: 0;
  z-index: 50;
  min-height: 66px;
  padding: 9px clamp(18px, 3.2vw, 46px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  color: #fff;
  background: rgba(7, 10, 16, .86);
  border-bottom: 1px solid rgba(255, 255, 255, .10);
  box-shadow: 0 8px 28px rgba(0, 0, 0, .12);
  backdrop-filter: saturate(160%) blur(22px);
  -webkit-backdrop-filter: saturate(160%) blur(22px);
}
.brand-lockup { display: flex; align-items: center; gap: 13px; min-width: 0; }
.company-logo-link {
  display: block;
  flex: 0 0 46px;
  border-radius: 12px;
  text-decoration: none;
}
.company-logo {
  display: block;
  width: 46px;
  height: 46px;
  flex: 0 0 46px;
  object-fit: contain;
  border: 1px solid rgba(255, 255, 255, .16);
  border-radius: 12px;
  box-shadow: 0 9px 24px rgba(0, 0, 0, .24);
  transition: transform .18s ease, border-color .18s ease, box-shadow .18s ease;
}
.company-logo-link:hover .company-logo { transform: translateY(-1px); border-color: rgba(142, 197, 255, .72); box-shadow: 0 12px 28px rgba(8, 120, 249, .26); }
.company-logo-link:active .company-logo { transform: translateY(0); }
.brand-title {
  overflow: hidden;
  color: #f8fafc;
  font-size: 15px;
  font-weight: 640;
  letter-spacing: -.015em;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.platform-nav { display: flex; align-items: center; justify-content: center; gap: 4px; padding: 4px; background: rgba(255, 255, 255, .07); border: 1px solid rgba(255, 255, 255, .08); border-radius: 999px; }
.platform-nav-link { padding: 7px 12px; color: rgba(255, 255, 255, .68); border-radius: 999px; font-size: 12px; font-weight: 620; text-decoration: none; white-space: nowrap; transition: color .18s ease, background .18s ease; }
.platform-nav-link:hover, .platform-nav-link:focus-visible { color: #fff; background: rgba(255, 255, 255, .10); }
.platform-nav-link.active { color: #fff; background: linear-gradient(135deg, rgba(8, 120, 249, .92), rgba(103, 92, 255, .86)); box-shadow: 0 5px 16px rgba(8, 120, 249, .24); }
.session { color: rgba(255, 255, 255, .68); font-size: 12px; text-align: right; overflow-wrap: anywhere; }
.session a { color: #fff; font-weight: 620; text-decoration: none; }
.session a:hover { color: #8ec5ff; }
main { width: min(1240px, calc(100% - 40px)); margin: 0 auto; padding: 42px 0 72px; }
h1, h2, h3, .value, .account-id { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", Inter, "Segoe UI", sans-serif; }
h1 { margin: 0; font-size: clamp(34px, 5vw, 56px); line-height: 1.02; letter-spacing: -.048em; }
h2 { margin: 32px 0 0; font-size: clamp(22px, 2.5vw, 28px); line-height: 1.15; letter-spacing: -.03em; }
h3 { letter-spacing: -.015em; }
a { color: var(--brand-deep); }
.muted { color: var(--muted); }
.label { color: var(--muted-strong); font-size: 10px; font-weight: 720; letter-spacing: .105em; text-transform: uppercase; }
.value { margin-top: 7px; font-size: 27px; font-weight: 690; line-height: 1.14; letter-spacing: -.033em; }
select, input[type=text], input[type=date], textarea {
  color: var(--ink);
  background: rgba(255, 255, 255, .92);
  border: 1px solid var(--line-strong);
  border-radius: 13px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .75), 0 1px 2px rgba(15, 23, 42, .03);
  font: inherit;
  transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
}
select, input[type=text], input[type=date] { min-width: 220px; max-width: 100%; margin-top: 7px; padding: 10px 13px; }
textarea { width: 100%; min-height: 92px; margin-top: 13px; padding: 13px 15px; resize: vertical; }
select:hover, input:hover, textarea:hover { border-color: rgba(8, 120, 249, .36); }
select:focus-visible, input:focus-visible, textarea:focus-visible, a:focus-visible, button:focus-visible { outline: none; box-shadow: 0 0 0 4px rgba(8, 120, 249, .18); }
.button {
  display: inline-flex;
  min-height: 40px;
  padding: 9px 15px;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(135deg, #111827 0%, #26344d 100%);
  border: 0;
  border-radius: 999px;
  box-shadow: 0 8px 20px rgba(15, 23, 42, .16);
  cursor: pointer;
  font: inherit;
  font-weight: 650;
  text-decoration: none;
  transition: transform .18s ease, box-shadow .18s ease, background .18s ease;
}
.button:hover { background: linear-gradient(135deg, var(--brand-deep), var(--accent)); box-shadow: 0 12px 28px rgba(8, 120, 249, .22); transform: translateY(-1px); }
.button:active { transform: translateY(0); }
.button:disabled { color: rgba(255, 255, 255, .8); background: #98a2b3; box-shadow: none; cursor: not-allowed; transform: none; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; }
}
@media (max-width: 650px) {
  header { position: relative; padding: 10px 16px; flex-wrap: wrap; }
  .company-logo-link { flex-basis: 42px; border-radius: 10px; }
  .company-logo { width: 42px; height: 42px; flex-basis: 42px; border-radius: 10px; }
  .brand-title { font-size: 14px; }
  .platform-nav { order: 3; width: 100%; justify-content: flex-start; overflow-x: auto; }
  .session { width: 100%; padding-left: 55px; margin-top: -9px; text-align: left; }
  main { width: min(100% - 28px, 1240px); padding-top: 28px; }
}
"""

HOME_THEME_CSS = COMMON_THEME_CSS + r"""
.home-hero { max-width: 820px; padding: 48px 0 12px; }
.eyebrow { display: inline-flex; margin-bottom: 18px; padding: 7px 11px; color: #075dbf; background: rgba(8, 120, 249, .09); border: 1px solid rgba(8, 120, 249, .14); border-radius: 999px; font-size: 10px; font-weight: 750; letter-spacing: .11em; text-transform: uppercase; }
.home-hero h1 { max-width: 760px; }
.home-hero .lead { max-width: 650px; margin-top: 17px; color: var(--muted-strong); font-size: 18px; line-height: 1.55; }
.accounts { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 20px; margin-top: 34px; }
.account-card {
  position: relative;
  display: block;
  min-height: 250px;
  overflow: hidden;
  padding: 28px;
  color: #fff;
  background: radial-gradient(circle at 88% 4%, rgba(103, 92, 255, .72), transparent 12rem), radial-gradient(circle at 5% 110%, rgba(8, 120, 249, .66), transparent 17rem), linear-gradient(145deg, #101522, #06080d 72%);
  border: 1px solid rgba(255, 255, 255, .12);
  border-radius: 28px;
  box-shadow: var(--shadow-lg);
  text-decoration: none;
  isolation: isolate;
  transition: transform .24s ease, box-shadow .24s ease;
}
.account-card::after { position: absolute; right: -46px; bottom: -70px; width: 210px; height: 210px; content: ""; border: 1px solid rgba(255, 255, 255, .12); border-radius: 50%; box-shadow: 0 0 0 34px rgba(255, 255, 255, .025), 0 0 0 70px rgba(255, 255, 255, .018); z-index: -1; }
.account-card:hover, .account-card:focus-visible { transform: translateY(-4px); box-shadow: 0 30px 80px rgba(15, 23, 42, .22); }
.account-badge { display: inline-block; margin-bottom: 56px; color: rgba(255, 255, 255, .62); font-size: 10px; font-weight: 720; letter-spacing: .1em; text-transform: uppercase; }
.account-id { margin-bottom: 7px; font-size: 30px; font-weight: 680; letter-spacing: -.04em; }
.account-card .muted { color: rgba(255, 255, 255, .64); }
.open { margin-top: 20px; color: #fff; font-size: 14px; font-weight: 650; }
.open span { display: inline-block; margin-left: 4px; transition: transform .18s ease; }
.account-card:hover .open span { transform: translateX(4px); }
"""

ACCOUNT_THEME_CSS = COMMON_THEME_CSS + r"""
.breadcrumbs { display: flex; align-items: center; gap: 8px; margin-bottom: 23px; color: var(--muted); font-size: 12px; }
.breadcrumbs a { color: var(--muted-strong); font-weight: 620; text-decoration: none; }
.breadcrumbs a:hover { color: var(--brand); }
.account-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
.account-hero-copy .muted { margin-top: 11px; font-size: 15px; }
.account-hero::after { display: block; width: clamp(90px, 17vw, 220px); height: 2px; margin-bottom: 13px; content: ""; background: linear-gradient(90deg, var(--brand), var(--accent), transparent); border-radius: 999px; }
.account-nav { display: flex; gap: 4px; overflow-x: auto; margin: 31px 0 0; padding: 7px; background: rgba(255, 255, 255, .67); border: 1px solid rgba(255, 255, 255, .85); border-radius: 17px; box-shadow: var(--shadow-sm); scrollbar-width: thin; backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); -webkit-overflow-scrolling: touch; }
.account-nav-link { flex: 0 0 auto; padding: 9px 12px; color: var(--muted-strong); border: 1px solid transparent; border-radius: 11px; font-size: 12px; font-weight: 640; text-decoration: none; white-space: nowrap; transition: color .18s ease, background .18s ease, box-shadow .18s ease; }
.account-nav-link:hover, .account-nav-link:focus-visible { color: var(--ink); background: rgba(255, 255, 255, .82); }
.account-nav-link.active { color: #fff; background: linear-gradient(135deg, #121826, #25334d); border-color: rgba(255, 255, 255, .12); box-shadow: 0 7px 17px rgba(15, 23, 42, .17); }
.account-toolbar { display: flex; align-items: end; justify-content: space-between; gap: 18px; margin-top: 18px; padding: 19px 22px; background: linear-gradient(135deg, rgba(255, 255, 255, .87), rgba(248, 250, 252, .70)); border: 1px solid rgba(255, 255, 255, .92); border-radius: 21px; box-shadow: var(--shadow-sm); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); }
.account-toolbar strong { display: block; margin-top: 5px; font-size: 17px; letter-spacing: -.02em; }
.account-toolbar { align-items: center; }
.account-toolbar .report-date-hidden { display: none; }
[hidden] { display: none !important; }
#benchmark-select + #benchmark { margin-top: 14px; }
#etl-note { margin-top: 18px; }
.account-toolbar label { min-width: 245px; }
.asof-select { width: 100%; min-width: 0; margin-top: 7px; font-size: 16px; font-weight: 660; }
.grid { margin: 17px 0 24px; }
.page-view { display: none; }
.page-view.active { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 18px; }
.card { position: relative; overflow: hidden; min-width: 0; padding: 24px; background: var(--surface); border: 1px solid rgba(255, 255, 255, .92); border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); backdrop-filter: saturate(135%) blur(20px); -webkit-backdrop-filter: saturate(135%) blur(20px); transition: border-color .2s ease, box-shadow .2s ease; }
.card:hover { border-color: rgba(8, 120, 249, .13); box-shadow: 0 18px 46px rgba(15, 23, 42, .08); }
.page-view[data-section="overview"] .card { min-height: 150px; padding-top: 31px; }
.page-view[data-section="overview"] .card::before { position: absolute; top: 0; left: 0; width: 100%; height: 3px; content: ""; background: linear-gradient(90deg, var(--brand), var(--accent), transparent 78%); }
.wide { grid-column: span 4; }
.admin-layout { display: grid; grid-column: 1 / -1; grid-template-columns: minmax(0, 1.7fr) minmax(320px, 1fr); gap: 18px; align-items: start; width: 100%; }
.admin-layout .card { min-height: 100%; }
.admin-overview .status-row { grid-template-columns: repeat(4, minmax(0, 1fr)); }
.admin-actions-panel select, .admin-actions-panel input { max-width: 100%; }
.loading { opacity: .58; pointer-events: none; }
.metrics, .status-row { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 18px; }
.metric, .status-item { min-width: 0; padding: 15px; background: linear-gradient(145deg, rgba(248, 250, 252, .94), rgba(255, 255, 255, .72)); border: 1px solid var(--line); border-radius: var(--radius-md); }
.metric .value { font-size: 19px; overflow-wrap: anywhere; }
.metric-guide-card { display: block; color: inherit; text-decoration: none; cursor: pointer; transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease; }
.metric-guide-card:hover { transform: translateY(-2px); border-color: rgba(8, 120, 249, .30); box-shadow: 0 10px 24px rgba(15, 23, 42, .08); }
.metric-guide-card:focus-visible { outline: 3px solid rgba(8, 120, 249, .24); outline-offset: 2px; }
.metric-guide-card .label span, .metric-guide-heading-link span, .metric-guide-inline span { color: var(--accent); font-size: .9em; }
.metric-guide-heading-link, .metric-guide-table-link, .metric-guide-inline { color: inherit; text-decoration-color: rgba(8, 120, 249, .32); text-underline-offset: 3px; }
.metric-guide-heading-link:hover, .metric-guide-table-link:hover, .metric-guide-inline:hover { color: var(--accent); text-decoration-color: currentColor; }
.metric-guide-table-link { color: var(--muted-strong); }
.status-item strong { display: block; margin-top: 5px; color: var(--ink-soft); overflow-wrap: anywhere; }
.benchmark-primary, .benchmark-picker { grid-column: span 2; }
.benchmark-picker { margin: 0; }
.benchmark-picker legend { padding: 0; }
.benchmark-help { margin: 8px 0 12px; color: var(--muted); font-size: 12px; line-height: 1.5; }
.benchmark-choice-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
.benchmark-choice {
  display: flex;
  min-width: 0;
  padding: 10px 11px;
  align-items: flex-start;
  gap: 10px;
  color: var(--ink-soft);
  background: rgba(255, 255, 255, .76);
  border: 1px solid var(--line);
  border-radius: 12px;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.45;
  transition: border-color .16s ease, background .16s ease, box-shadow .16s ease;
}
.benchmark-choice:hover { background: #fff; border-color: rgba(8, 120, 249, .32); }
.benchmark-choice:has(input:checked) { color: #0b4ca3; background: rgba(8, 120, 249, .08); border-color: rgba(8, 120, 249, .34); box-shadow: 0 4px 13px rgba(8, 120, 249, .08); }
.benchmark-choice.disabled { color: var(--muted); background: rgba(241, 245, 249, .72); cursor: not-allowed; opacity: .68; }
.benchmark-choice input { flex: 0 0 auto; margin: 2px 0 0; accent-color: var(--brand); }
.benchmark-choice span { min-width: 0; overflow-wrap: anywhere; }
.benchmark-picker-footer { display: flex; margin-top: 12px; align-items: center; justify-content: space-between; gap: 10px; }
.benchmark-selection-summary { color: var(--muted-strong); font-size: 12px; font-weight: 650; overflow-wrap: anywhere; }
.benchmark-clear { padding: 6px 10px; color: var(--brand-deep); background: rgba(8, 120, 249, .07); border: 1px solid rgba(8, 120, 249, .16); border-radius: 999px; cursor: pointer; font: inherit; font-size: 11px; font-weight: 700; white-space: nowrap; }
.benchmark-clear:hover:not(:disabled) { background: rgba(8, 120, 249, .13); }
.benchmark-clear:disabled { color: var(--muted); cursor: default; opacity: .58; }
@media (max-width: 900px) {
  .benchmark-primary, .benchmark-picker { grid-column: 1 / -1; }
  .benchmark-choice-grid { grid-template-columns: 1fr; }
}
.chart-wrap { position: relative; width: 100%; margin-top: 14px; padding: 8px 4px 0; }
canvas { display: block; width: 100%; height: 320px; }
.legend { display: flex; gap: 20px; margin-top: 11px; color: var(--muted-strong); font-size: 12px; }
.swatch { display: inline-block; width: 20px; height: 4px; margin: 0 7px 2px 0; border-radius: 99px; }
.portfolio { background: var(--chart-portfolio); }
.index { background: var(--chart-benchmark); }
.chart-tooltip { display: none; position: absolute; z-index: 2; min-width: 190px; padding: 10px 12px; color: #f8fafc; background: rgba(8, 12, 20, .93); border: 1px solid rgba(255, 255, 255, .14); border-radius: 12px; box-shadow: 0 16px 38px rgba(0, 0, 0, .25); font-size: 12px; line-height: 1.55; pointer-events: none; backdrop-filter: blur(14px); }
table { width: 100%; border-collapse: separate; border-spacing: 0; }
th, td { padding: 12px 11px; border-bottom: 1px solid var(--line); text-align: right; vertical-align: top; }
th { color: var(--muted-strong); background: rgba(248, 250, 252, .74); font-size: 10px; font-weight: 720; letter-spacing: .07em; text-transform: uppercase; }
tbody tr { transition: background .16s ease; }
tbody tr:hover { background: rgba(8, 120, 249, .035); }
th:first-child, td:first-child, th:nth-child(2), td:nth-child(2) { text-align: left; }
.table-scroll { overflow-x: auto; margin-top: 16px; border: 1px solid var(--line); border-radius: var(--radius-md); }
.trade-table { min-width: 1180px; }
.trade-table th:nth-child(3), .trade-table td:nth-child(3), .trade-table th:nth-child(4), .trade-table td:nth-child(4) { text-align: left; }
.metric-guide-intro .value { margin-bottom: 8px; }
.methodology-callout { margin: 18px 0; padding: 16px 18px; color: var(--text); background: linear-gradient(135deg, rgba(8, 120, 249, .08), rgba(103, 92, 255, .08)); border: 1px solid rgba(8, 120, 249, .14); border-radius: var(--radius-md); line-height: 1.6; }
.methodology-callout.secondary { background: rgba(245, 247, 251, .78); border-color: var(--line); }
.metric-search-label { display: block; max-width: 620px; margin-top: 18px; }
.metric-search-label input { width: 100%; }
.metric-guide-section[hidden], .metric-definition[hidden] { display: none; }
.metric-guide-section > .muted { max-width: 920px; margin: 8px 0 0; }
.metric-guide-list { display: grid; gap: 14px; margin-top: 18px; }
.metric-definition { padding: 20px; background: rgba(249, 250, 252, .82); border: 1px solid var(--line); border-radius: var(--radius-md); scroll-margin-top: 86px; }
.metric-definition:target { background: linear-gradient(135deg, rgba(8, 120, 249, .10), rgba(103, 92, 255, .07)); border-color: rgba(8, 120, 249, .42); box-shadow: 0 0 0 4px rgba(8, 120, 249, .08), 0 18px 42px rgba(15, 23, 42, .09); }
.metric-definition:target .metric-definition-heading h3::after { margin-left: 9px; color: var(--accent); content: "Selected explanation"; font-size: 10px; font-weight: 750; letter-spacing: .04em; text-transform: uppercase; vertical-align: middle; }
.metric-definition-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding-bottom: 13px; border-bottom: 1px solid var(--line); }
.metric-definition-heading h3 { margin: 0; font-size: 18px; letter-spacing: -.01em; }
.metric-definition-heading span { flex: 0 0 auto; padding: 5px 9px; color: #2653a5; background: rgba(8, 120, 249, .08); border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .02em; }
.metric-explanation-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; padding-top: 16px; }
.metric-explanation-grid p { margin: 7px 0 0; color: var(--text); font-size: 13px; line-height: 1.62; }
.metric-explanation-label { color: var(--muted); font-size: 11px; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.metric-technical { margin-top: 15px; padding-top: 13px; border-top: 1px solid var(--line); }
.metric-technical summary { width: fit-content; color: var(--accent); cursor: pointer; font-size: 12px; font-weight: 700; }
.metric-technical summary:hover { text-decoration: underline; }
.metric-technical div { margin-top: 11px; padding: 12px 14px; background: #fff; border: 1px solid var(--line); border-radius: 11px; }
.metric-technical code { color: #25314d; white-space: normal; overflow-wrap: anywhere; font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace; font-size: 12px; line-height: 1.65; }
@media (max-width: 900px) { .metric-explanation-grid { grid-template-columns: 1fr; gap: 14px; } }
@media (max-width: 600px) { .metric-definition-heading { align-items: flex-start; flex-direction: column; } .metric-definition { padding: 16px; } }
.warning { margin-top: 16px; padding: 13px 15px; color: #725100; background: linear-gradient(135deg, #fff8e5, #fffdf7); border: 1px solid rgba(194, 137, 0, .27); border-radius: 15px; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 15px; }
.empty { padding: 24px; color: var(--muted); text-align: center; }
.contributor-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }
.contributor-grid > div { min-width: 0; padding: 17px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: var(--radius-md); }
.contributor-grid h3 { margin: 0 0 8px; font-size: 14px; }
.contributor-grid table td:last-child, .contributor-grid table th:last-child { text-align: right; }
.filter-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: 15px; }
.filter-grid label { display: flex; min-width: 0; flex-direction: column; }
.filter-grid input, .filter-grid select { width: 100%; min-width: 0; }
.ai-chat-card { overflow: hidden; padding: 0; min-height: 720px; }
.admin-live-chat { grid-column: 1 / -1; width: 100%; }
.admin-live-chat .ai-chat-sidebar { background: linear-gradient(180deg, rgba(244, 247, 252, .98), rgba(237, 243, 250, .88)); }
.admin-chat-badge { display: inline-flex; min-width: 18px; min-height: 18px; margin-left: 7px; padding: 1px 5px; align-items: center; justify-content: center; color: #fff; background: var(--brand); border-radius: 999px; font-size: 10px; font-weight: 760; vertical-align: middle; }
.admin-message-source { margin-left: 5px; color: var(--muted); font-size: 10px; }
.admin-action-preview { margin-top: 15px; padding: 15px; background: linear-gradient(135deg, rgba(8, 120, 249, .07), rgba(103, 92, 255, .07)); border: 1px solid rgba(8, 120, 249, .18); border-radius: 15px; }
.admin-action-preview code { overflow-wrap: anywhere; }
.ai-chat-shell { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: 720px; }
.ai-chat-sidebar { min-width: 0; overflow: hidden; padding: 20px; background: linear-gradient(180deg, rgba(248, 250, 252, .96), rgba(242, 245, 250, .82)); border-right: 1px solid var(--line); }
.ai-chat-sidebar-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
.ai-chat-sidebar-head .button { min-height: 34px; padding: 7px 11px; font-size: 12px; }
.ai-conversation-list { display: grid; min-width: 0; gap: 8px; margin-top: 17px; }
.ai-conversation-item { display: block; width: 100%; min-width: 0; max-width: 100%; overflow: hidden; padding: 12px; color: var(--text); background: rgba(255, 255, 255, .72); border: 1px solid transparent; border-radius: 13px; cursor: pointer; text-align: left; }
.ai-conversation-item:hover, .ai-conversation-item.active { background: #fff; border-color: rgba(8, 120, 249, .22); box-shadow: 0 7px 20px rgba(15, 23, 42, .06); }
.ai-conversation-item strong { display: -webkit-box; max-width: 100%; overflow: hidden; color: var(--ink-soft); font-size: 13px; line-height: 1.35; overflow-wrap: anywhere; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
.ai-conversation-item span { display: block; margin-top: 4px; color: var(--muted); font-size: 11px; }
.ai-chat-main { display: flex; min-width: 0; flex-direction: column; background: rgba(255, 255, 255, .74); }
.ai-chat-header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 19px 22px; border-bottom: 1px solid var(--line); }
.ai-chat-header-title { min-width: 0; }
.ai-chat-header-title strong { display: block; overflow: hidden; font-size: 16px; text-overflow: ellipsis; white-space: nowrap; }
.ai-chat-header-title .muted { margin-top: 4px; font-size: 11px; }
.ai-chat-header .actions { flex: 0 0 auto; margin: 0; }
.ai-chat-header .button { min-height: 34px; padding: 7px 11px; font-size: 11px; }
.ai-chat-thread { flex: 1; min-height: 470px; max-height: 690px; overflow-y: auto; padding: 24px; scroll-behavior: smooth; }
.ai-chat-empty { max-width: 620px; margin: 70px auto; text-align: center; }
.ai-chat-empty .value { font-size: 25px; }
.ai-chat-empty .muted { margin-top: 11px; line-height: 1.65; }
.ai-prompt-chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; margin-top: 20px; }
.ai-prompt-chip { padding: 8px 11px; color: #25436e; background: rgba(8, 120, 249, .07); border: 1px solid rgba(8, 120, 249, .14); border-radius: 999px; cursor: pointer; font: inherit; font-size: 11px; }
.ai-message { display: flex; margin: 0 0 20px; }
.ai-message.user { justify-content: flex-end; }
.ai-message-bubble { max-width: min(820px, 88%); padding: 15px 17px; border-radius: 18px; line-height: 1.6; }
.ai-message.user .ai-message-bubble { color: #fff; background: linear-gradient(135deg, #0878f9, #675cff); border-bottom-right-radius: 5px; box-shadow: 0 12px 26px rgba(8, 120, 249, .18); }
.ai-message.assistant .ai-message-bubble { width: min(900px, 96%); max-width: 96%; background: #fff; border: 1px solid var(--line); border-bottom-left-radius: 5px; box-shadow: var(--shadow-sm); }
.ai-message-meta { margin-top: 8px; color: var(--muted); font-size: 10px; }
.ai-message.user .ai-message-meta { color: rgba(255, 255, 255, .68); }
.ai-answer h3 { margin: 0 0 9px; font-size: 18px; }
.ai-answer p { margin: 8px 0; }
.ai-answer .metrics { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.ai-artifact { margin-top: 18px; padding-top: 17px; border-top: 1px solid var(--line); }
.ai-artifact-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.ai-artifact-heading .actions { margin: 0; }
.ai-artifact-heading .button, .ai-answer-downloads .button { min-height: 32px; padding: 6px 10px; font-size: 11px; }
.ai-answer-downloads { margin-top: 14px; }
.ai-typing { display: inline-flex; gap: 5px; padding: 16px 18px; background: #fff; border: 1px solid var(--line); border-radius: 18px 18px 18px 5px; }
.ai-typing i { width: 7px; height: 7px; background: #98a2b3; border-radius: 50%; animation: ai-pulse 1.1s infinite ease-in-out; }
.ai-typing i:nth-child(2) { animation-delay: .15s; }
.ai-typing i:nth-child(3) { animation-delay: .3s; }
@keyframes ai-pulse { 0%, 80%, 100% { opacity: .35; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-4px); } }
.ai-chat-composer { padding: 17px 21px 20px; background: rgba(248, 250, 252, .94); border-top: 1px solid var(--line); }
.ai-chat-composer textarea { min-height: 76px; margin: 0; background: #fff; }
.ai-composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }
.ai-composer-footer .muted { font-size: 10px; }
.ai-composer-footer .button { min-width: 112px; }
.onboarding-case { margin-top: 18px; padding: 20px; background: rgba(248, 250, 252, .78); border: 1px solid var(--line); border-radius: var(--radius-md); }
.onboarding-case-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.onboarding-case-head h3 { margin: 4px 0 0; font-size: 22px; }
.onboarding-definition { margin: 14px 0 4px; padding: 14px 16px; background: #fff; border: 1px solid var(--line); border-radius: 12px; }
.onboarding-definition p { margin: 7px 0 0; line-height: 1.55; }
.onboarding-definition .muted { overflow-wrap: anywhere; }
.onboarding-pill { display: inline-flex; padding: 7px 11px; color: #245092; background: rgba(8, 120, 249, .09); border: 1px solid rgba(8, 120, 249, .17); border-radius: 999px; font-size: 11px; font-weight: 760; }
.onboarding-pill.blocked { color: #8a5a00; background: #fff8e5; border-color: rgba(194, 137, 0, .27); }
.onboarding-pill.active { color: #17643a; background: #ecfdf3; border-color: rgba(23, 100, 58, .20); }
.onboarding-checks { display: grid; gap: 9px; margin-top: 18px; }
.onboarding-check { display: grid; grid-template-columns: minmax(170px, .45fr) minmax(0, 1fr); gap: 14px; padding: 11px 13px; background: #fff; border: 1px solid var(--line); border-left: 4px solid #98a2b3; border-radius: 12px; }
.onboarding-check.pass { border-left-color: #2f9e62; }
.onboarding-check.warn { border-left-color: #d99a15; }
.onboarding-check.fail { border-left-color: #d9485f; }
.onboarding-check span { color: var(--muted); font-size: 12px; line-height: 1.5; }
.onboarding-case h4 { margin: 20px 0 0; font-size: 13px; }
@media (max-width: 900px) {
  .page-view.active { grid-template-columns: 1fr 1fr; }
  .wide { grid-column: span 2; }
  .metrics, .status-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-grid { grid-template-columns: 1fr 1fr; }
  .admin-layout { grid-template-columns: 1fr; }
  .ai-chat-shell { grid-template-columns: 220px minmax(0, 1fr); }
  .ai-answer .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 650px) {
  .account-hero::after { display: none; }
  .account-nav { margin-top: 24px; border-radius: 15px; }
  .account-toolbar { align-items: stretch; flex-direction: column; padding: 17px; border-radius: 18px; }
  .account-toolbar label { min-width: 0; }
  .page-view.active { grid-template-columns: 1fr; }
  .wide { grid-column: span 1; }
  .metrics, .status-row, .filter-grid, .contributor-grid { grid-template-columns: 1fr; }
  .card { padding: 19px; border-radius: 21px; }
  .value { font-size: 24px; }
  .ai-chat-card { min-height: 0; }
  .ai-chat-shell { display: block; min-height: 0; }
  .ai-chat-sidebar { border-right: 0; border-bottom: 1px solid var(--line); }
  .ai-conversation-list { display: flex; overflow-x: auto; }
  .ai-conversation-item { flex: 0 0 210px; }
  .ai-chat-header { align-items: flex-start; flex-direction: column; }
  .ai-chat-thread { min-height: 430px; max-height: none; padding: 16px; }
  .ai-message-bubble, .ai-message.assistant .ai-message-bubble { width: 100%; max-width: 100%; }
  .ai-answer .metrics { grid-template-columns: 1fr; }
  .ai-artifact-heading, .ai-composer-footer { align-items: flex-start; flex-direction: column; }
  .onboarding-case-head { align-items: flex-start; flex-direction: column; }
  .onboarding-check { grid-template-columns: 1fr; gap: 5px; }
}
"""
