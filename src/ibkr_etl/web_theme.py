"""Shared visual system for the WealthRyse portfolio portal."""

import base64
from functools import lru_cache

from .web_fonts import WEB_FONTS_BASE64

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
  --chart-grid: #e7ebf2;
  --chart-axis: #98a2b3;
  --chart-text: #667085;
  --chart-point-ring: #fff;
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
.platform-menu { position: relative; }
.platform-menu summary { display: block; cursor: pointer; list-style: none; }
.platform-menu summary::-webkit-details-marker { display: none; }
.platform-menu summary::after { margin-left: 6px; content: "▾"; font-size: 10px; }
.platform-menu-list { position: absolute; right: 0; top: calc(100% + 8px); z-index: 60; display: grid; min-width: 200px; gap: 2px; padding: 6px; background: rgba(12, 16, 24, .97); border: 1px solid rgba(255, 255, 255, .12); border-radius: 14px; box-shadow: 0 18px 40px rgba(0, 0, 0, .3); }
.platform-menu-link { padding: 8px 11px; color: rgba(255, 255, 255, .78); border-radius: 9px; font-size: 12px; font-weight: 620; text-decoration: none; white-space: nowrap; }
.platform-menu-link:hover, .platform-menu-link:focus-visible { color: #fff; background: rgba(255, 255, 255, .10); }
.platform-menu-link.active { color: #fff; background: rgba(8, 120, 249, .55); }
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
td, .value, .metric, .account-stats { font-variant-numeric: tabular-nums; }
.gain { color: #0f7a4a; }
.loss { color: #b42318; }
.rsi-flag { margin-left: 6px; color: var(--muted-strong); font-size: 10px; font-weight: 720; letter-spacing: .06em; text-transform: uppercase; }
select, input[type=text], input[type=search], input[type=date], input.secret-field, textarea {
  color: var(--ink);
  background: rgba(255, 255, 255, .92);
  border: 1px solid var(--line-strong);
  border-radius: 13px;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .75), 0 1px 2px rgba(15, 23, 42, .03);
  font: inherit;
  transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
}
select, input[type=text], input[type=search], input[type=date], input.secret-field { min-width: 220px; max-width: 100%; margin-top: 7px; padding: 10px 13px; }
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
  .platform-nav { order: 3; width: 100%; justify-content: flex-start; flex-wrap: wrap; border-radius: 18px; }
  .platform-menu-list { left: 0; right: auto; }
  .session { width: 100%; padding-left: 55px; margin-top: -9px; text-align: left; }
  main { width: min(100% - 28px, 1240px); padding-top: 28px; }
}
"""

DARK_THEME_CSS = r"""
@media screen {
  :root {
    color-scheme: dark;
    --ink: #e6ebf3;
    --ink-soft: #c5cddb;
    --muted: #94a0b3;
    --muted-strong: #adb7c7;
    --line: rgba(226, 232, 240, .10);
    --line-strong: rgba(226, 232, 240, .18);
    --brand: #4f9dff;
    --brand-deep: #8bbcff;
    --accent: #9a92ff;
    --chart-portfolio: #7f9ef5;
    --chart-benchmark: #f0a443;
    --chart-grid: rgba(226, 232, 240, .09);
    --chart-axis: rgba(226, 232, 240, .28);
    --chart-text: #94a0b3;
    --chart-point-ring: #141a26;
    --bg: #0b0f17;
    --surface: rgba(21, 27, 39, .88);
    --surface-soft: rgba(27, 34, 48, .9);
    --shadow-sm: 0 1px 2px rgba(0, 0, 0, .35), 0 7px 24px rgba(0, 0, 0, .28);
    --shadow-lg: 0 22px 60px rgba(0, 0, 0, .45);
  }
  html { background: var(--bg); }
  body { background: radial-gradient(circle at 12% -8%, rgba(79, 157, 255, .10), transparent 31rem), radial-gradient(circle at 94% 4%, rgba(154, 146, 255, .08), transparent 27rem), linear-gradient(180deg, #0e131d 0, var(--bg) 31rem, var(--bg) 100%); }
  .account-card { background: radial-gradient(circle at 88% 4%, rgba(103, 92, 255, .55), transparent 12rem), radial-gradient(circle at 5% 110%, rgba(8, 120, 249, .5), transparent 17rem), linear-gradient(145deg, #1a2336, #0f1520 72%); border-color: rgba(255, 255, 255, .14); }
  .gain { color: #57c994; }
  .loss { color: #ff8a80; }
  select, input[type=text], input[type=search], input[type=date], input.secret-field, textarea { background: rgba(12, 16, 25, .92); box-shadow: none; }
  .button:disabled { color: rgba(255, 255, 255, .6); background: #3a4456; }
  .eyebrow { color: #8bbcff; background: rgba(79, 157, 255, .12); border-color: rgba(79, 157, 255, .22); }
  .accounts-summary .behind { color: #fcd34d; }
  .card { border-color: rgba(255, 255, 255, .07); }
  .card:hover { border-color: rgba(79, 157, 255, .22); box-shadow: 0 18px 46px rgba(0, 0, 0, .35); }
  .account-nav, .section-jumps, .metric-guide-toolbar { background: rgba(19, 25, 36, .92); border-color: rgba(255, 255, 255, .08); }
  .account-nav-link:hover, .account-nav-link:focus-visible, .section-jumps a:hover, .section-jumps a:focus-visible { background: rgba(255, 255, 255, .07); }
  .account-nav-link.active { background: linear-gradient(135deg, #2f3f5f, #41557c); border-color: rgba(255, 255, 255, .10); box-shadow: 0 7px 17px rgba(0, 0, 0, .3); }
  .section-jumps a.active { background: rgba(79, 157, 255, .16); }
  .metric, .status-item { background: linear-gradient(145deg, rgba(31, 38, 54, .92), rgba(24, 30, 44, .82)); }
  .metric-guide-card:hover { border-color: rgba(79, 157, 255, .35); box-shadow: 0 10px 24px rgba(0, 0, 0, .3); }
  th, .total-row td { background: rgba(31, 38, 54, .75); }
  tbody tr:hover { background: rgba(79, 157, 255, .06); }
  .benchmark-choice { background: rgba(255, 255, 255, .04); }
  .benchmark-choice:hover { background: rgba(255, 255, 255, .08); }
  .benchmark-choice:has(input:checked) { color: #b5d4ff; background: rgba(79, 157, 255, .14); }
  .benchmark-choice.disabled { background: rgba(255, 255, 255, .02); }
  .methodology-callout.secondary, .metric-definition, .metric-conventions dl div, .onboarding-case { background: rgba(27, 34, 48, .82); }
  .metric-technical div, .onboarding-definition, .onboarding-check { background: rgba(14, 19, 29, .9); }
  .metric-definition-heading span { color: #b5d4ff; background: rgba(79, 157, 255, .14); }
  .metric-technical code, .metric-versions code { color: #d5dcea; }
  .metric-copy.copied { color: #57c994; background: rgba(87, 201, 148, .12); }
  .warning { color: #f5cf6b; background: linear-gradient(135deg, rgba(194, 137, 0, .16), rgba(194, 137, 0, .07)); }
  .ai-chat-sidebar, .admin-live-chat .ai-chat-sidebar, .ai-chat-composer { background: rgba(17, 22, 33, .95); }
  .ai-chat-main { background: rgba(21, 27, 39, .8); }
  .ai-conversation-item:hover, .ai-conversation-item.active, .ai-message.assistant .ai-message-bubble, .ai-typing, .ai-chat-composer textarea { background: rgba(31, 38, 54, .95); }
  .ai-prompt-chip { color: #b5d4ff; background: rgba(79, 157, 255, .12); }
  .onboarding-pill { color: #b5d4ff; background: rgba(79, 157, 255, .14); }
  .onboarding-pill.blocked { color: #fcd34d; background: rgba(194, 137, 0, .16); }
  .onboarding-pill.active { color: #6ee7a8; background: rgba(87, 201, 148, .14); }
  .sector-track { background: rgba(255, 255, 255, .08); }
  .freshness { color: #6ee7a8; background: rgba(87, 201, 148, .12); border-color: rgba(87, 201, 148, .26); }
  .freshness.late { color: #fcd34d; background: rgba(252, 211, 77, .10); border-color: rgba(252, 211, 77, .26); }
  .freshness.stale, .page-error { color: #ff9b94; background: rgba(255, 138, 128, .09); border-color: rgba(255, 138, 128, .26); }
  .range-buttons, .chart-mode { background: rgba(255, 255, 255, .06); }
  .range-buttons button:hover, .chart-mode button:hover { background: rgba(255, 255, 255, .08); }
  .chart-mode button.active { background: #41557c; }
  .skeleton-line { background: linear-gradient(90deg, rgba(255, 255, 255, .05), rgba(255, 255, 255, .11), rgba(255, 255, 255, .05)); background-size: 200% 100%; }
}
"""


# Mission theme: a black-and-white, typographic look inspired by spacex.com.
# It is appended after the shared and dark rules, so it wins on equal
# specificity. Colours come from the custom properties, so the same rules
# serve light mode, dark mode (the "@media screen" blocks) and print.
WEB_FONT_FILES = frozenset(WEB_FONTS_BASE64)


@lru_cache(maxsize=None)
def web_font_bytes(name: str) -> bytes:
    return base64.b64decode(WEB_FONTS_BASE64[name])


_FONT_FACE_CSS = "".join(
    f'@font-face {{ font-family: "WR Mission"; font-style: normal; font-weight: {weight}; '
    f'font-display: swap; src: url("/assets/fonts/barlow-{weight}.woff2") format("woff2"); }}\n'
    for weight in (400, 500, 600, 700)
)

_STARFIELD = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='480' height='480' fill='white'%3E%3Ccircle cx='217' cy='269' r='1.2' fill-opacity='0.55'/%3E%3Ccircle cx='244' cy='282' r='0.5' fill-opacity='0.77'/%3E%3Ccircle cx='228' cy='295' r='0.5' fill-opacity='0.31'/%3E%3Ccircle cx='146' cy='44' r='1.2' fill-opacity='0.83'/%3E%3Ccircle cx='305' cy='286' r='0.7' fill-opacity='0.88'/%3E%3Ccircle cx='314' cy='295' r='0.5' fill-opacity='0.66'/%3E%3Ccircle cx='399' cy='30' r='0.5' fill-opacity='0.37'/%3E%3Ccircle cx='116' cy='14' r='0.7' fill-opacity='0.46'/%3E%3Ccircle cx='284' cy='94' r='0.5' fill-opacity='0.67'/%3E%3Ccircle cx='240' cy='318' r='0.7' fill-opacity='0.68'/%3E%3Ccircle cx='195' cy='265' r='1.2' fill-opacity='0.3'/%3E%3Ccircle cx='122' cy='364' r='0.8' fill-opacity='0.44'/%3E%3Ccircle cx='34' cy='368' r='0.7' fill-opacity='0.32'/%3E%3Ccircle cx='140' cy='32' r='0.5' fill-opacity='0.8'/%3E%3Ccircle cx='0' cy='101' r='0.5' fill-opacity='0.56'/%3E%3Ccircle cx='471' cy='191' r='0.5' fill-opacity='0.62'/%3E%3Ccircle cx='95' cy='324' r='0.6' fill-opacity='0.31'/%3E%3Ccircle cx='160' cy='463' r='1.2' fill-opacity='0.85'/%3E%3Ccircle cx='65' cy='339' r='0.5' fill-opacity='0.29'/%3E%3Ccircle cx='383' cy='85' r='0.8' fill-opacity='0.37'/%3E%3Ccircle cx='244' cy='473' r='1.2' fill-opacity='0.34'/%3E%3Ccircle cx='309' cy='56' r='0.7' fill-opacity='0.89'/%3E%3Ccircle cx='0' cy='415' r='1.2' fill-opacity='0.64'/%3E%3Ccircle cx='479' cy='9' r='0.5' fill-opacity='0.51'/%3E%3Ccircle cx='410' cy='308' r='0.5' fill-opacity='0.28'/%3E%3Ccircle cx='70' cy='212' r='0.5' fill-opacity='0.75'/%3E%3Ccircle cx='158' cy='142' r='0.5' fill-opacity='0.3'/%3E%3Ccircle cx='100' cy='306' r='0.5' fill-opacity='0.64'/%3E%3Ccircle cx='178' cy='218' r='0.8' fill-opacity='0.56'/%3E%3Ccircle cx='276' cy='416' r='0.5' fill-opacity='0.66'/%3E%3Ccircle cx='149' cy='110' r='0.8' fill-opacity='0.41'/%3E%3Ccircle cx='91' cy='355' r='0.8' fill-opacity='0.38'/%3E%3Ccircle cx='456' cy='423' r='0.8' fill-opacity='0.3'/%3E%3Ccircle cx='23' cy='52' r='0.8' fill-opacity='0.88'/%3E%3Ccircle cx='114' cy='338' r='0.6' fill-opacity='0.52'/%3E%3Ccircle cx='434' cy='236' r='0.8' fill-opacity='0.36'/%3E%3Ccircle cx='346' cy='33' r='0.5' fill-opacity='0.56'/%3E%3Ccircle cx='314' cy='296' r='0.5' fill-opacity='0.43'/%3E%3Ccircle cx='440' cy='98' r='0.5' fill-opacity='0.29'/%3E%3Ccircle cx='197' cy='120' r='0.5' fill-opacity='0.36'/%3E%3Ccircle cx='177' cy='275' r='0.5' fill-opacity='0.31'/%3E%3Ccircle cx='66' cy='216' r='0.6' fill-opacity='0.68'/%3E%3Ccircle cx='332' cy='281' r='0.5' fill-opacity='0.63'/%3E%3Ccircle cx='443' cy='228' r='0.6' fill-opacity='0.71'/%3E%3Ccircle cx='462' cy='10' r='1' fill-opacity='0.3'/%3E%3Ccircle cx='32' cy='149' r='0.5' fill-opacity='0.9'/%3E%3Ccircle cx='36' cy='262' r='1' fill-opacity='0.28'/%3E%3Ccircle cx='449' cy='354' r='0.5' fill-opacity='0.77'/%3E%3Ccircle cx='439' cy='169' r='1' fill-opacity='0.56'/%3E%3Ccircle cx='37' cy='412' r='1.2' fill-opacity='0.27'/%3E%3Ccircle cx='240' cy='7' r='1' fill-opacity='0.5'/%3E%3Ccircle cx='280' cy='292' r='0.5' fill-opacity='0.31'/%3E%3Ccircle cx='55' cy='123' r='0.7' fill-opacity='0.72'/%3E%3Ccircle cx='186' cy='353' r='0.8' fill-opacity='0.55'/%3E%3Ccircle cx='222' cy='260' r='0.8' fill-opacity='0.74'/%3E%3Ccircle cx='14' cy='289' r='0.7' fill-opacity='0.26'/%3E%3Ccircle cx='460' cy='54' r='1.2' fill-opacity='0.65'/%3E%3Ccircle cx='442' cy='123' r='0.5' fill-opacity='0.49'/%3E%3Ccircle cx='69' cy='294' r='0.8' fill-opacity='0.36'/%3E%3Ccircle cx='435' cy='317' r='0.7' fill-opacity='0.57'/%3E%3Ccircle cx='116' cy='194' r='0.6' fill-opacity='0.38'/%3E%3Ccircle cx='207' cy='387' r='0.5' fill-opacity='0.82'/%3E%3Ccircle cx='185' cy='280' r='0.6' fill-opacity='0.39'/%3E%3Ccircle cx='65' cy='168' r='1.2' fill-opacity='0.28'/%3E%3Ccircle cx='31' cy='471' r='1.2' fill-opacity='0.36'/%3E%3Ccircle cx='216' cy='132' r='0.5' fill-opacity='0.79'/%3E%3Ccircle cx='184' cy='250' r='1' fill-opacity='0.45'/%3E%3Ccircle cx='403' cy='471' r='0.7' fill-opacity='0.46'/%3E%3Ccircle cx='398' cy='133' r='0.8' fill-opacity='0.28'/%3E%3Ccircle cx='340' cy='274' r='0.6' fill-opacity='0.67'/%3E%3C/svg%3E"

MISSION_THEME_CSS = _FONT_FACE_CSS + r"""
:root {
  --font-mission: "WR Mission", "D-DIN", Bahnschrift, "DIN Alternate", "DIN Next", "Helvetica Neue", Arial, sans-serif;
  --ink: #0a0a0a;
  --ink-soft: #2b2b2b;
  --muted: #6b6b6b;
  --muted-strong: #474747;
  --line: rgba(0, 0, 0, .12);
  --line-strong: rgba(0, 0, 0, .26);
  --brand: #000;
  --brand-deep: #000;
  --accent: #4a5d73;
  --chart-portfolio: #111;
  --chart-benchmark: #4f7bb0;
  --chart-grid: rgba(0, 0, 0, .08);
  --chart-axis: rgba(0, 0, 0, .3);
  --chart-text: #6b6b6b;
  --chart-point-ring: #fff;
  --bg: #fff;
  --surface: #fff;
  --surface-soft: #f6f6f6;
  --mission-raised: #efefef;
  --mission-hover: rgba(0, 0, 0, .04);
  --gain: #11804e;
  --loss: #c4281c;
  --warn-ink: #7a5000;
  --shadow-sm: none;
  --shadow-lg: none;
  --radius-md: 2px;
  --radius-lg: 3px;
}
html, body { background: var(--bg); }
body { color: var(--ink); font-family: var(--font-mission); font-size: 15px; letter-spacing: .01em; }
h1, h2, h3, .value, .account-id { font-family: var(--font-mission); }
h1 { font-size: clamp(34px, 5.2vw, 64px); font-weight: 700; line-height: 1; letter-spacing: .015em; text-transform: uppercase; }
h2 { font-size: clamp(20px, 2.2vw, 26px); font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
h3 { font-weight: 600; letter-spacing: .03em; }
a { color: var(--ink); text-underline-offset: 4px; }
.label { color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .2em; }
.value { font-weight: 500; letter-spacing: 0; }
.gain { color: var(--gain); }
.loss { color: var(--loss); }

/* Header: a black bar in both modes, uppercase navigation with an underline marker. */
header { min-height: 72px; padding: 10px clamp(18px, 3.4vw, 52px); background: rgba(0, 0, 0, .96); border-bottom: 1px solid rgba(255, 255, 255, .1); box-shadow: none; }
header a:focus-visible, header button:focus-visible { outline-color: #fff; }
.company-logo { width: 42px; height: 42px; border-color: rgba(255, 255, 255, .22); border-radius: 4px; box-shadow: none; }
.company-logo-link { flex-basis: 42px; border-radius: 4px; }
.company-logo-link:hover .company-logo { border-color: #fff; box-shadow: none; transform: none; }
.brand-title { color: #fff; font-size: 13px; font-weight: 600; letter-spacing: .2em; text-transform: uppercase; }
.platform-nav { gap: 2px; padding: 0; background: none; border: 0; border-radius: 0; }
.platform-nav-link, .platform-menu summary.platform-nav-link { position: relative; padding: 10px 12px; color: rgba(255, 255, 255, .72); background: none; border-radius: 0; font-size: 12px; font-weight: 600; letter-spacing: .16em; text-transform: uppercase; }
.platform-nav-link::before { position: absolute; left: 12px; right: 12px; bottom: 2px; height: 2px; content: ""; background: #fff; transform: scaleX(0); transform-origin: left; transition: transform .25s ease; }
.platform-nav-link:hover, .platform-nav-link:focus-visible { color: #fff; background: none; }
.platform-nav-link:hover::before, .platform-nav-link:focus-visible::before, .platform-nav-link.active::before { transform: scaleX(1); }
.platform-nav-link.active { color: #fff; background: none; box-shadow: none; }
.platform-menu-list { background: #000; border: 1px solid rgba(255, 255, 255, .18); border-radius: 0; box-shadow: none; }
.platform-menu-link { border-radius: 0; font-size: 11px; letter-spacing: .14em; text-transform: uppercase; }
.platform-menu-link.active { background: rgba(255, 255, 255, .12); }
.session { color: rgba(255, 255, 255, .6); font-size: 11px; letter-spacing: .08em; }
.session a { color: #fff; letter-spacing: .14em; text-transform: uppercase; }
.session a:hover { color: #fff; text-decoration: underline; }
.theme-toggle { margin-left: 10px; padding: 5px 10px; color: #fff; background: transparent; border: 1px solid rgba(255, 255, 255, .4); border-radius: 0; cursor: pointer; font: inherit; font-size: 10px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; white-space: nowrap; transition: background .2s ease, color .2s ease; }
.theme-toggle:hover { color: #000; background: #fff; }
main { width: min(1320px, calc(100% - 48px)); }

/* Buttons: the spacex.com outline button that fills in on hover. */
.button { min-height: 44px; padding: 11px 24px; color: var(--ink); background: transparent; border: 2px solid var(--ink); border-radius: 0; box-shadow: none; font-size: 12px; font-weight: 700; letter-spacing: .18em; text-transform: uppercase; transition: background .2s ease, color .2s ease, border-color .2s ease; }
.button:hover { color: var(--bg); background: var(--ink); box-shadow: none; transform: none; }
.button:disabled { color: var(--muted); background: transparent; border-color: var(--line-strong); }
select, input[type=text], input[type=search], input[type=date], input.secret-field, textarea { color: var(--ink); background: var(--surface); border: 1px solid var(--line-strong); border-radius: 0; box-shadow: none; }
select:hover, input:hover, textarea:hover { border-color: var(--muted); }
select:focus-visible, input:focus-visible, textarea:focus-visible { border-color: var(--ink); box-shadow: none; }
a:focus-visible, button:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; box-shadow: none; }
input[type=number], .page-view[data-section] input[type=number] { color: var(--ink); background: var(--surface); border: 1px solid var(--line-strong); border-radius: 0; box-shadow: none; }

/* Surfaces: flat panels with hairline borders. */
.card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius-lg); box-shadow: none; backdrop-filter: none; -webkit-backdrop-filter: none; }
.card:hover { border-color: var(--line-strong); box-shadow: none; }
.page-view[data-section="overview"] .card::before { width: 56px; height: 2px; background: var(--ink); }
.metric, .status-item { background: transparent; border: 0; border-top: 1px solid var(--line-strong); border-radius: 0; padding: 16px 2px 6px; }
.metric .value { font-size: 26px; font-weight: 500; }
.metric-guide-card:hover { border-color: var(--ink); box-shadow: none; transform: none; }
.warning { color: var(--warn-ink); background: rgba(242, 207, 115, .08); border: 1px solid rgba(194, 137, 0, .35); border-radius: 0; }
.onboarding-row, .methodology-callout, .methodology-callout.secondary, .metric-definition, .onboarding-case, .agent-diagnosis, .health-incident { background: var(--surface-soft); border-color: var(--line); border-radius: var(--radius-md); }
.eyebrow { display: inline-flex; align-items: center; gap: 12px; margin-bottom: 18px; padding: 0; color: var(--muted-strong); background: none; border: 0; border-radius: 0; font-size: 12px; font-weight: 600; letter-spacing: .26em; }
.eyebrow::before { width: 32px; height: 1px; content: ""; background: currentColor; }
.freshness, .freshness.late, .freshness.stale, .onboarding-pill, .ops-pill, .live-source, .live-flag { border-radius: 0; letter-spacing: .08em; text-transform: uppercase; font-size: 11px; }
.page-error { border-radius: 0; }

/* Account pages: uppercase title, tab bar with an underline marker. */
.breadcrumbs { font-size: 11px; letter-spacing: .16em; text-transform: uppercase; }
.breadcrumbs a { color: var(--muted-strong); }
.breadcrumbs a:hover { color: var(--ink); }
.account-hero h1 { font-size: clamp(28px, 3.8vw, 48px); letter-spacing: .02em; }
.account-hero::after { display: none; }
.account-nav { gap: 0; padding: 0; background: var(--bg); border: 0; border-bottom: 1px solid var(--line); border-radius: 0; box-shadow: none; }
.account-nav-link { padding: 16px 14px 14px; color: var(--muted-strong); border: 0; border-radius: 0; font-size: 11px; font-weight: 600; letter-spacing: .18em; text-transform: uppercase; }
.account-nav-link:hover, .account-nav-link:focus-visible { color: var(--ink); background: none; }
.account-nav-link.active { color: var(--ink); background: none; border: 0; box-shadow: inset 0 -2px 0 var(--ink); }
.section-jumps, .metric-guide-toolbar { background: var(--bg); border-color: var(--line); border-radius: 0; }
.section-jumps a { font-size: 11px; letter-spacing: .14em; text-transform: uppercase; }
.section-jumps a, .metric-jump a, .range-buttons button, .chart-mode button, .benchmark-choice, .benchmark-clear, .metric-expand, .metric-copy { border-radius: 0; }
.range-buttons, .chart-mode { background: none; border: 1px solid var(--line); border-radius: 0; }
.range-buttons button, .chart-mode button { letter-spacing: .12em; text-transform: uppercase; font-size: 11px; }
.range-buttons button.active, .chart-mode button.active { color: var(--bg); background: var(--ink); }
.spark-area { fill: var(--chart-portfolio); fill-opacity: .07; }
.sector-track { height: 4px; background: var(--line); border-radius: 0; }
.sector-fill { background: var(--chart-portfolio); border-radius: 0; }
.table-scroll { border-color: var(--line); border-radius: 0; }
th { color: var(--muted); background: transparent; font-size: 10px; font-weight: 600; letter-spacing: .18em; }
th, .total-row td { background: transparent; }
tbody tr:hover { background: var(--mission-hover); }
.chart-tooltip { color: #f5f5f5; background: #000; border-color: rgba(255, 255, 255, .3); border-radius: 0; box-shadow: none; }
.ai-chat-sidebar, .admin-live-chat .ai-chat-sidebar, .ai-chat-composer { background: var(--surface-soft); }
.ai-chat-main { background: var(--surface); }
.ai-conversation-item:hover, .ai-conversation-item.active, .ai-message.assistant .ai-message-bubble, .ai-typing, .ai-chat-composer textarea { background: var(--mission-raised); }
.ai-prompt-chip { color: var(--ink); background: transparent; border: 1px solid var(--line-strong); border-radius: 0; letter-spacing: .04em; }
.ai-prompt-chip:hover { color: var(--bg); background: var(--ink); }

@media (max-width: 650px) {
  header { padding: 10px 16px; }
  .platform-nav { flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; }
  .platform-nav-link { padding: 10px 10px; font-size: 11px; letter-spacing: .12em; }
  .brand-title { font-size: 12px; letter-spacing: .12em; }
  .account-hero h1 { font-size: 28px; }
  .session { padding-left: 55px; }
  main { width: min(100% - 32px, 1320px); }
}

/* Account subpages: one subpage of a section shows at a time, and the tab bar
   lists each section's subpages in a SpaceX-style dropdown. */
.part-off { display: none !important; }
.account-nav-item { position: relative; display: flex; flex: 0 0 auto; }
.nav-caret { display: inline-block; margin-left: 6px; font-size: 9px; letter-spacing: 0; opacity: .7; transition: transform .2s ease; }
.account-nav-menu { position: absolute; top: 100%; left: 0; z-index: 60; display: none; min-width: 220px; padding: 10px 0 12px; background: var(--bg); border: 1px solid var(--line); border-top: 0; box-shadow: 0 18px 36px rgba(0, 0, 0, .35); }
.account-nav-menu a { display: block; padding: 9px 18px; color: var(--muted-strong); font-size: 12px; font-weight: 600; letter-spacing: .16em; text-decoration: none; text-transform: uppercase; white-space: nowrap; }
.account-nav-menu a:hover, .account-nav-menu a:focus-visible { color: var(--ink); }
.account-nav-menu a[aria-current="page"] { color: var(--ink); box-shadow: inset 2px 0 0 var(--ink); }
.subpage-row { display: none; }
.subpage-row a[aria-current="page"] { color: var(--ink); box-shadow: inset 0 -2px 0 var(--ink); }
@media (min-width: 651px) {
  .account-nav { overflow: visible; }
  .account-nav-item:hover .account-nav-menu, .account-nav-item:focus-within .account-nav-menu { display: block; }
  .account-nav-item:hover .nav-caret, .account-nav-item:focus-within .nav-caret { transform: rotate(180deg); }
}
@media (max-width: 650px) {
  .subpage-row { display: flex; }
  .nav-caret { display: none; }
}

/* Portfolio analyst chat: its own styles assume a light card and a blue accent with white text,
   so restate them on the mission colours. "body" outranks the chat's later-loaded rules. */
body .wr-panel { background: var(--bg); color: var(--ink); border: 1px solid var(--line-strong); border-radius: var(--radius-lg); box-shadow: 0 18px 48px rgba(0, 0, 0, .45); font-family: var(--font-mission); }
body .wr-head { padding: 14px 16px 8px; }
body .wr-title strong { font-size: 13px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; }
body .wr-sub { color: var(--muted); opacity: 1; }
body .wr-icons button:hover { background: var(--mission-hover); }
body .wr-bar { border-bottom-color: var(--line); }
body .wr-bar a, body .wr-bar button, body .wr-explain, body .wr-rate button { color: var(--ink); background: transparent; border-color: var(--line-strong); border-radius: 0; }
body .wr-bar a:hover, body .wr-bar button:hover, body .wr-chip:hover, body .wr-explain:hover { border-color: var(--ink); }
body .wr-msg { border-radius: var(--radius-md); font-size: 15px; line-height: 1.55; }
body .wr-bot { background: var(--mission-raised); color: var(--ink); border: 1px solid var(--line); }
body .wr-user { background: var(--ink); color: var(--bg); }
body .wr-chip { background: var(--bg); color: var(--ink); border-color: var(--line-strong); border-radius: 0; font-size: 14px; }
body .wr-status, body .wr-cite, body .wr-sources, body .wr-hint { color: var(--muted-strong); opacity: 1; }
body .wr-form { border-top-color: var(--line); }
body .wr-form textarea { color: var(--ink); background: var(--surface-soft); border-color: var(--line-strong); border-radius: 0; font-size: 15px; }
body .wr-form textarea::placeholder { color: var(--muted); }
body .wr-fab, body .wr-form button { background: var(--ink); color: var(--bg); border: 1px solid var(--ink); border-radius: 0; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; }
body .wr-fab { font-size: 12px; box-shadow: 0 8px 24px rgba(0, 0, 0, .35); }
body .wr-form button { font-size: 12px; }
body .wr-fab:hover, body .wr-form button:hover:not(:disabled) { background: var(--bg); color: var(--ink); }
body .wr-form button:disabled { opacity: .45; }
body .wr-fab:focus-visible, body .wr-panel button:focus-visible, body .wr-panel a:focus-visible { outline-color: var(--ink); }
body .wr-alert { color: var(--warn-ink); }
body .wr-table th, body .wr-table td { border-bottom-color: var(--line); }
body .wr-table thead th { background: var(--mission-raised); }

/* Dark mode, the default. Every "@media screen" block is the dark theme, which
   the header's theme switch turns off for people who choose light mode. */
@media screen {
  :root {
    color-scheme: dark;
    --ink: #f5f5f5;
    --ink-soft: #d6d6d6;
    --muted: #8c8c8c;
    --muted-strong: #b3b3b3;
    --line: rgba(255, 255, 255, .12);
    --line-strong: rgba(255, 255, 255, .26);
    --brand: #fff;
    --brand-deep: #fff;
    --accent: #a7b8cc;
    --chart-portfolio: #f2f2f2;
    --chart-benchmark: #6f8fb3;
    --chart-grid: rgba(255, 255, 255, .08);
    --chart-axis: rgba(255, 255, 255, .28);
    --chart-text: #8c8c8c;
    --chart-point-ring: #000;
    --bg: #000;
    --surface: #080808;
    --surface-soft: #0d0d0d;
    --mission-raised: #161616;
    --mission-hover: rgba(255, 255, 255, .04);
    --gain: #5fd49a;
    --loss: #ff7b72;
    --warn-ink: #f2cf73;
  }
  html, body { background: var(--bg); }
  .warning { background: rgba(242, 207, 115, .06); border-color: rgba(242, 207, 115, .3); }
}
"""

HOME_MISSION_CSS = MISSION_THEME_CSS + r"""
/* Full-bleed hero: starfield over the curve of the Earth, dark in both modes. */
.home-hero { position: relative; isolation: isolate; display: flex; flex-direction: column; justify-content: flex-end; max-width: none; width: 100vw; min-height: min(74vh, 720px); margin: -42px 0 0 calc(50% - 50vw); padding: 120px max(24px, calc(50vw - 660px)) 64px; overflow: hidden; color: #fff; }
.home-hero::before { position: absolute; inset: 0; z-index: -2; content: ""; background: url("__STARFIELD__") 0 0 / 480px 480px repeat, #000; }
.home-hero::after { position: absolute; inset: 0; z-index: -1; content: ""; background:
  linear-gradient(180deg, transparent 80%, var(--bg) 100%),
  radial-gradient(ellipse 95% 62% at 78% 128%, #0d2a50 0, #071a33 55%, #030a14 97%, transparent 100%),
  radial-gradient(ellipse 95% 62% at 78% 128%, transparent 96.4%, rgba(170, 210, 255, .85) 98.2%, rgba(80, 150, 240, .35) 101%, rgba(40, 100, 210, .12) 106%, transparent 116%),
  linear-gradient(90deg, rgba(0, 0, 0, .7) 0, rgba(0, 0, 0, .2) 50%, transparent 100%); }
.home-hero h1 { max-width: 900px; color: #fff; font-size: clamp(44px, 7.4vw, 96px); }
.home-hero .eyebrow { color: rgba(255, 255, 255, .78); }
.home-hero .lead { max-width: 560px; margin-top: 18px; color: rgba(255, 255, 255, .78); font-size: 18px; letter-spacing: .02em; }
.accounts-summary { margin-top: 26px; color: rgba(255, 255, 255, .72); font-size: 12px; letter-spacing: .16em; text-transform: uppercase; }
.accounts-summary .behind { color: #f2cf73; }
.accounts { grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 0; margin-top: 56px; border-top: 1px solid var(--line); }
.account-card { padding: 34px 0 30px; color: var(--ink); background: none; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; box-shadow: none; transition: none; }
.account-card::after { display: none; }
.account-card:hover, .account-card:focus-visible { transform: none; box-shadow: none; }
.account-card .muted { color: var(--muted); }
.account-card .gain { color: var(--gain); }
.account-card .loss { color: var(--loss); }
.account-card .behind { color: var(--warn-ink); }
.account-badge { margin-bottom: 14px; color: var(--muted); font-size: 11px; font-weight: 600; letter-spacing: .22em; }
.account-id { font-size: clamp(22px, 2.6vw, 32px); font-weight: 600; letter-spacing: .01em; }
.account-stats { margin-top: 22px; padding-top: 0; border-top: 0; }
.account-nav-value { font-size: clamp(34px, 4vw, 52px); font-weight: 500; letter-spacing: 0; }
.account-returns { gap: 26px; font-size: 16px; }
.account-stat-label { color: var(--muted); letter-spacing: .2em; }
.open { align-self: flex-start; margin-top: 26px; padding: 12px 26px; color: var(--ink); border: 2px solid var(--ink); font-size: 12px; font-weight: 700; letter-spacing: .2em; text-transform: uppercase; transition: background .2s ease, color .2s ease; }
.account-card:hover .open, .account-card:focus-visible .open { color: var(--bg); background: var(--ink); }
.account-card:hover .open span { transform: none; }
@media (max-width: 650px) {
  .home-hero { min-height: 64vh; margin-top: -28px; padding: 96px 16px 44px; }
  .home-hero::after { background:
    linear-gradient(180deg, transparent 82%, var(--bg) 100%),
    radial-gradient(ellipse 160% 46% at 70% 122%, #0d2a50 0, #071a33 55%, #030a14 97%, transparent 100%),
    radial-gradient(ellipse 160% 46% at 70% 122%, transparent 96.4%, rgba(170, 210, 255, .85) 98.2%, rgba(80, 150, 240, .35) 101%, transparent 112%),
    linear-gradient(180deg, transparent 0, rgba(0, 0, 0, .35) 100%); }
}
""".replace("__STARFIELD__", _STARFIELD)

# The header's light/dark switch. Dark is the default; a person's choice is kept
# in this browser. Light mode turns off every "@media screen" rule (the dark
# theme), and a style added later in the page is caught when the page finishes
# loading. Pages with canvas charts reload so the charts pick up the new colours.
THEME_TOGGLE_HTML = r"""<button class="theme-toggle" type="button" data-theme-toggle>Light mode</button><script>
(()=>{const key='wr-theme',off=[],button=document.currentScript.previousElementSibling;
const stored=()=>{try{return localStorage.getItem(key)==='light'?'light':'dark'}catch(error){return'dark'}};
const apply=mode=>{document.documentElement.dataset.theme=mode;button.textContent=mode==='light'?'Dark mode':'Light mode';button.setAttribute('aria-label',mode==='light'?'Switch to dark mode':'Switch to light mode');
  if(mode==='dark'){off.splice(0).forEach(rule=>{rule.media.mediaText='screen'});return}
  for(const sheet of document.styleSheets){let rules;try{rules=sheet.cssRules}catch(error){continue}
    for(const rule of rules)if(rule instanceof CSSMediaRule&&rule.media.mediaText==='screen'){rule.media.mediaText='not all';off.push(rule)}}};
apply(stored());
document.addEventListener('DOMContentLoaded',()=>apply(stored()));
button.addEventListener('click',()=>{const mode=stored()==='light'?'dark':'light';try{localStorage.setItem(key,mode)}catch(error){}
  if(document.querySelector('canvas'))location.reload();else apply(mode)});
})();
</script>"""


HOME_THEME_CSS = COMMON_THEME_CSS + r"""
.home-hero { max-width: 820px; padding: 48px 0 12px; }
.eyebrow { display: inline-flex; margin-bottom: 18px; padding: 7px 11px; color: #075dbf; background: rgba(8, 120, 249, .09); border: 1px solid rgba(8, 120, 249, .14); border-radius: 999px; font-size: 10px; font-weight: 750; letter-spacing: .11em; text-transform: uppercase; }
.home-hero h1 { max-width: 760px; }
.home-hero .lead { max-width: 650px; margin-top: 17px; color: var(--muted-strong); font-size: 18px; line-height: 1.55; }
.accounts { display: grid; grid-template-columns: repeat(auto-fit, minmax(290px, 1fr)); gap: 20px; margin-top: 34px; }
.account-card {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
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
.account-badge { display: inline-block; margin-bottom: 26px; color: rgba(255, 255, 255, .62); font-size: 10px; font-weight: 720; letter-spacing: .1em; text-transform: uppercase; }
.account-id { margin-bottom: 7px; font-size: 30px; font-weight: 680; letter-spacing: -.04em; }
.account-card .muted { color: rgba(255, 255, 255, .64); }
.open { margin-top: 20px; color: #fff; font-size: 14px; font-weight: 650; }
.open span { display: inline-block; margin-left: 4px; transition: transform .18s ease; }
.account-card:hover .open span { transform: translateX(4px); }
.account-stats { display: grid; gap: 4px; margin-top: 14px; padding-top: 14px; border-top: 1px solid rgba(255, 255, 255, .12); }
.account-nav-value { min-height: 30px; font-size: 24px; font-weight: 680; letter-spacing: -.03em; }
.account-returns { display: flex; flex-wrap: wrap; gap: 16px; font-size: 14px; font-weight: 650; }
.account-stat-label { color: rgba(255, 255, 255, .58); font-size: 10px; font-weight: 720; letter-spacing: .1em; }
.account-card .gain { color: #6ee7a8; }
.account-card .loss { color: #ff9b94; }
.account-card .behind { color: #fcd34d; }
.account-since { margin-top: 10px; font-size: 12px; }
.accounts-summary { display: flex; flex-wrap: wrap; gap: 8px 22px; margin-top: 16px; color: var(--muted-strong); font-size: 14px; }
.accounts-summary:empty { display: none; }
.accounts-summary .behind { color: #9a6700; font-weight: 650; }
.home-hero { padding-bottom: 0; }
""" + DARK_THEME_CSS + HOME_MISSION_CSS

ACCOUNT_THEME_CSS = COMMON_THEME_CSS + r"""
.breadcrumbs { display: flex; align-items: center; gap: 8px; margin-bottom: 23px; color: var(--muted); font-size: 12px; }
.breadcrumbs a { color: var(--muted-strong); font-weight: 620; text-decoration: none; }
.breadcrumbs a:hover { color: var(--brand); }
.account-hero { display: flex; align-items: end; justify-content: space-between; gap: 24px; }
.account-hero-copy .muted { margin-top: 11px; font-size: 15px; }
.account-hero h1 { font-size: clamp(28px, 3.6vw, 42px); line-height: 1.08; letter-spacing: -.035em; text-wrap: balance; }
.account-hero::after { display: block; width: clamp(90px, 17vw, 220px); height: 2px; margin-bottom: 13px; content: ""; background: linear-gradient(90deg, var(--brand), var(--accent), transparent); border-radius: 999px; }
.account-nav { display: flex; gap: 4px; overflow-x: auto; margin: 31px 0 0; padding: 7px; background: rgba(255, 255, 255, .67); border: 1px solid rgba(255, 255, 255, .85); border-radius: 17px; box-shadow: var(--shadow-sm); scrollbar-width: thin; position: sticky; top: 76px; z-index: 40; background: rgba(255, 255, 255, .9); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); -webkit-overflow-scrolling: touch; }
.account-nav-link { flex: 0 0 auto; padding: 9px 12px; color: var(--muted-strong); border: 1px solid transparent; border-radius: 11px; font-size: 12px; font-weight: 640; text-decoration: none; white-space: nowrap; transition: color .18s ease, background .18s ease, box-shadow .18s ease; }
.account-nav-link:hover, .account-nav-link:focus-visible { color: var(--ink); background: rgba(255, 255, 255, .82); }
.account-nav-link.active { color: #fff; background: linear-gradient(135deg, #121826, #25334d); border-color: rgba(255, 255, 255, .12); box-shadow: 0 7px 17px rgba(15, 23, 42, .17); }
[hidden] { display: none !important; }
#benchmark-select + #benchmark { margin-top: 14px; }
#etl-note { margin-top: 18px; }
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
.benchmark-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 10px; }
.benchmark-chip { display: inline-flex; align-items: center; padding: 5px 10px; color: var(--muted-strong); background: transparent; border: 1px solid var(--line); border-radius: 999px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; opacity: .55; }
.benchmark-chip[aria-pressed="true"] { color: var(--ink); opacity: 1; border-color: var(--muted-strong); }
.benchmark-chip:hover { opacity: 1; }
.benchmark-chip-actions { display: inline-flex; gap: 6px; }
.benchmark-chart-note:empty { display: none; }
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
.metric-guide-toolbar { position: sticky; top: 76px; z-index: 30; padding: 14px 20px 12px; background: rgba(255, 255, 255, .95); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); }
.metric-guide-toolbar-row { display: flex; align-items: center; gap: 12px; }
.metric-guide-toolbar .metric-search-label { flex: 1 1 auto; min-width: 0; max-width: none; margin-top: 0; }
.metric-guide-toolbar #metric-search-status { margin-top: 6px; font-size: 12px; }
.metric-guide-toolbar input { min-width: 0; }
.metric-guide-toolbar .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.metric-expand, .metric-copy { flex: 0 0 auto; color: var(--brand-deep); background: rgba(8, 120, 249, .07); border: 1px solid rgba(8, 120, 249, .16); border-radius: 999px; cursor: pointer; font: inherit; font-weight: 700; white-space: nowrap; }
.metric-expand { padding: 9px 14px; font-size: 12px; }
.metric-copy { padding: 5px 10px; font-size: 11px; }
.metric-expand:hover, .metric-copy:hover { background: rgba(8, 120, 249, .13); }
.metric-expand[aria-pressed="true"] { color: #fff; background: var(--brand-deep); border-color: var(--brand-deep); }
.metric-copy.copied { color: #067647; background: rgba(6, 118, 71, .08); border-color: rgba(6, 118, 71, .22); }
.metric-jump { display: flex; gap: 6px; overflow-x: auto; margin-top: 10px; padding-bottom: 2px; scrollbar-width: thin; -webkit-overflow-scrolling: touch; }
.metric-jump a { flex: 0 0 auto; padding: 6px 10px; color: var(--muted-strong); background: var(--surface); border: 1px solid var(--line); border-radius: 999px; font-size: 12px; font-weight: 640; text-decoration: none; white-space: nowrap; }
.metric-jump a:hover, .metric-jump a:focus-visible { color: var(--ink); border-color: rgba(8, 120, 249, .32); }
.metric-jump a span { margin-left: 4px; color: var(--accent); font-weight: 720; }
.metric-jump a.empty { opacity: .45; }
@media (min-width: 900px) { .metric-jump { flex-wrap: wrap; overflow-x: visible; } }
.metric-guide-section, .metric-definition { scroll-margin-top: 220px; }
.metric-guide-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.metric-guide-section-head h2 { margin: 0; }
.metric-guide-count { color: var(--muted); font-size: 12px; white-space: nowrap; }
.metric-where, .metric-aliases, .metric-related { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 12px; font-size: 12px; }
.metric-where span, .metric-aliases span, .metric-related span { color: var(--muted); font-size: 11px; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.metric-aliases em { padding: 2px 8px; color: var(--ink-soft); background: rgba(103, 92, 255, .07); border-radius: 999px; font-style: normal; font-weight: 600; }
.metric-where { margin-top: 8px; color: var(--ink-soft); font-weight: 600; }
.metric-related { padding-top: 12px; border-top: 1px dashed var(--line); }
.metric-related a { color: var(--brand-deep); font-weight: 600; text-decoration-color: rgba(8, 120, 249, .32); text-underline-offset: 3px; }
.metric-related a:not(:last-child)::after { margin-left: 6px; color: var(--muted); content: "·"; }
.metric-back { display: inline-block; margin-top: 16px; color: var(--muted-strong); font-size: 12px; font-weight: 650; text-decoration: none; }
.metric-back:hover { color: var(--accent); }
.metric-conventions { margin-top: 4px; }
.metric-conventions summary { width: fit-content; color: var(--ink-soft); cursor: pointer; font-weight: 700; }
.metric-conventions dl { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin: 14px 0 0; }
.metric-conventions dl div { padding: 12px 14px; background: rgba(248, 250, 252, .9); border: 1px solid var(--line); border-radius: 14px; }
.metric-conventions dt { color: var(--muted); font-size: 11px; font-weight: 760; letter-spacing: .06em; text-transform: uppercase; }
.metric-conventions dd { margin: 5px 0 0; font-size: 13px; line-height: 1.55; }
.metric-versions { margin-top: 14px; font-size: 13px; }
.metric-versions ul { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 6px 0 0; padding: 0; list-style: none; color: var(--muted-strong); }
.metric-versions code { color: #25314d; font-size: 12px; }
@media (max-width: 900px) { .metric-conventions dl { grid-template-columns: 1fr 1fr; } }
@media (max-width: 650px) {
  .metric-guide-toolbar { top: 0; padding: 10px 12px; }
  .metric-expand { padding: 9px 11px; }
  .metric-guide-section, .metric-definition { scroll-margin-top: 140px; }
  .metric-conventions dl { grid-template-columns: 1fr; }
}
@media print {
  header, nav, .breadcrumbs, .account-hero-controls, .metric-guide-toolbar, .metric-copy, .metric-back { display: none !important; }
  body { background: #fff; }
  .card { box-shadow: none; backdrop-filter: none; border-color: #ddd; }
  .metric-definition { break-inside: avoid; background: #fff; }
  .metric-guide-section { break-before: page; }
  .metric-related a { color: inherit; text-decoration: none; }
}
@media (max-width: 600px) { .metric-definition-heading { align-items: flex-start; } .metric-definition { padding: 16px; } }
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
.onboarding-row { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 10px; padding: 13px 15px; background: var(--surface-soft); border: 1px solid var(--line); border-radius: 12px; }
.onboarding-row strong { display: block; }
.onboarding-row .muted { margin-top: 3px; font-size: 12px; }
.onboarding-row .actions { margin: 0; flex-wrap: wrap; justify-content: flex-end; }
@media (max-width: 650px) { .onboarding-row { flex-direction: column; align-items: flex-start; } .onboarding-row .actions { justify-content: flex-start; } }
@media (max-width: 900px) {
  .page-view.active { grid-template-columns: 1fr 1fr; }
  .wide { grid-column: span 2; }
  .metrics, .status-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-grid { grid-template-columns: 1fr 1fr; }
  .admin-layout { grid-template-columns: 1fr; }
  .ai-chat-shell { grid-template-columns: 220px minmax(0, 1fr); }
  .ai-answer .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
.overview-nav-head { display: flex; align-items: center; justify-content: space-between; gap: 24px; }
.overview-nav-copy { min-width: 0; }
.overview-nav-line { display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 14px; }
.overview-nav-line .value { font-size: 34px; }
.overview-day { font-size: 15px; font-weight: 650; }
.overview-spark { flex: 0 1 340px; width: 340px; max-width: 45%; height: 70px; }
.spark-area { fill: rgba(38, 70, 166, .10); }
.spark-line { fill: none; stroke: var(--chart-portfolio); stroke-width: 1.8; vector-effect: non-scaling-stroke; }
.spark-dot { fill: var(--chart-portfolio); }
.overview-returns { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.overview-returns .value { font-size: 20px; }
.overview-half { grid-column: span 2; display: flex; flex-direction: column; }
.overview-more { margin-top: auto; padding-top: 14px; font-size: 12px; font-weight: 650; text-decoration: none; }
.sector-bars { display: grid; gap: 9px; margin-top: 16px; }
.sector-row { display: grid; grid-template-columns: minmax(120px, 1.2fr) minmax(80px, 2fr) 64px; align-items: center; gap: 12px; font-size: 13px; }
.sector-row:has(.sector-meta) { grid-template-columns: minmax(130px, 1.1fr) minmax(90px, 2fr) 64px minmax(150px, 1fr); }
.sector-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sector-track { height: 8px; overflow: hidden; background: rgba(15, 23, 42, .06); border-radius: 99px; }
.sector-fill { display: block; height: 100%; background: linear-gradient(90deg, var(--chart-portfolio), var(--brand)); border-radius: 99px; }
.sector-weight { font-variant-numeric: tabular-nums; font-weight: 650; text-align: right; }
.sector-meta { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; text-align: right; }
.sector-detail { margin-top: 16px; }
.sector-detail summary { width: fit-content; color: var(--brand-deep); cursor: pointer; font-size: 12px; font-weight: 650; }
.sector-note { margin-top: 14px; font-size: 12px; }
.freshness { display: inline-block; margin-top: 12px; padding: 5px 11px; line-height: 1.5; color: #0b5d3b; background: rgba(15, 122, 74, .09); border: 1px solid rgba(15, 122, 74, .18); border-radius: 12px; font-size: 12px; font-weight: 650; }
.freshness::before { display: inline-block; width: 7px; height: 7px; margin: 0 7px 1px 0; content: ""; background: currentColor; border-radius: 50%; }
.freshness.late { color: #8a5a00; background: rgba(194, 137, 0, .10); border-color: rgba(194, 137, 0, .26); }
.freshness.stale { color: #9f1d14; background: rgba(180, 35, 24, .08); border-color: rgba(180, 35, 24, .22); }
.freshness a { color: inherit; }
.account-switcher { display: flex; min-width: 0; flex-direction: column; }
.account-hero:has(.account-switcher)::after { display: none; }
.account-switcher select { min-width: 240px; }
.range-buttons { display: inline-flex; gap: 3px; margin-top: 12px; padding: 3px; background: rgba(15, 23, 42, .045); border-radius: 10px; }
.range-buttons button { padding: 5px 10px; color: var(--muted-strong); background: transparent; border: 0; border-radius: 8px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; }
.range-buttons button:hover { color: var(--ink); background: rgba(255, 255, 255, .7); }
.range-buttons button.active { color: #fff; background: var(--chart-portfolio); }
.skeleton-line.short { width: 60%; margin-top: 10px; }
.skeleton-line { height: 14px; background: linear-gradient(90deg, rgba(15, 23, 42, .05), rgba(15, 23, 42, .10), rgba(15, 23, 42, .05)); background-size: 200% 100%; border-radius: 7px; animation: skeleton 1.2s ease-in-out infinite; }
@keyframes skeleton { from { background-position: 100% 0; } to { background-position: -100% 0; } }
.table-tools { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin-top: 14px; }
.table-tools input { min-width: 260px; margin-top: 0; }
table.sortable thead th { cursor: pointer; user-select: none; white-space: nowrap; }
table.sortable thead th:hover { color: var(--ink); }
table.sortable thead th::after { display: inline-block; width: 1em; margin-left: 4px; color: var(--muted); content: "↕"; opacity: .35; }
table.sortable thead th[aria-sort="ascending"]::after { content: "↑"; opacity: 1; color: var(--brand-deep); }
table.sortable thead th[aria-sort="descending"]::after { content: "↓"; opacity: 1; color: var(--brand-deep); }
.total-row td { background: rgba(248, 250, 252, .9); border-top: 1px solid var(--line-strong); }
.weight-cell { display: inline-flex; align-items: center; justify-content: flex-end; gap: 8px; min-width: 96px; }
.weight-bar { display: inline-block; height: 6px; background: var(--brand); border-radius: 3px; opacity: .7; }
@media (max-width: 900px) {
  .overview-returns { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .overview-half { grid-column: 1 / -1; }
}
@media (max-width: 650px) {
  .overview-nav-head { align-items: flex-start; flex-direction: column; }
  .overview-spark { flex: none; width: 100%; max-width: 100%; height: 64px; }
  .metrics.overview-returns { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .sector-row, .sector-row:has(.sector-meta) { grid-template-columns: minmax(0, 1fr) 64px; }
  .sector-track, .sector-meta { display: none; }
  .table-tools input { min-width: 0; width: 100%; }
  .account-hero::after { display: none; }
  .account-hero { align-items: stretch; flex-direction: column; gap: 16px; }
  .account-switcher select { min-width: 0; width: 100%; }
  .account-nav { top: 0; margin-top: 24px; border-radius: 15px; }
  .account-hero h1 { font-size: 28px; }
  table.stack-mobile, table.stack-mobile.trade-table { min-width: 0; }
  table.stack-mobile thead { display: none; }
  table.stack-mobile tbody tr { display: grid; grid-template-columns: 1fr 1fr; gap: 4px 14px; padding: 12px 14px; border-bottom: 1px solid var(--line); }
  table.stack-mobile tbody tr[hidden] { display: none; }
  table.stack-mobile tbody td { display: flex; flex-direction: column; padding: 0; border: 0; text-align: left; }
  table.stack-mobile tbody td::before { color: var(--muted); content: attr(data-label); font-size: 10px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
  table.stack-mobile tbody td:first-child { grid-column: 1 / -1; font-size: 15px; }
  table.stack-mobile tbody td:first-child::before, table.stack-mobile tbody td:not([data-label])::before, table.stack-mobile tbody td[data-label=""]::before { display: none; }
  table.stack-mobile tbody td[colspan] { grid-column: 1 / -1; }
  table.stack-mobile tbody td:empty { display: none; }
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
.half-card { grid-column: span 2; }
.half-card canvas { height: 240px; }
#drawdown-chart { height: 260px; }
table.monthly-returns { min-width: 860px; }
table.monthly-returns th, table.monthly-returns td { padding: 9px 8px; text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.monthly-returns th.text-col { text-align: left; }
.heat-cell { color: var(--ink); font-size: 12.5px; font-weight: 600; }
.heat-cell sup { margin-left: 1px; color: var(--muted-strong); font-size: 9px; }
.year-cell { border-left: 1px solid var(--line-strong); font-weight: 750; }
.analytics-methodology { font-size: 12px; line-height: 1.6; }
.sector-fill.negative { background: linear-gradient(90deg, #b42318, #e5484d); }
table.correlation-matrix th, table.correlation-matrix td { padding: 8px; text-align: center; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.correlation-matrix th.text-col { text-align: left; }
.corr-cell { font-size: 12.5px; font-weight: 600; }
@media (max-width: 900px) { .half-card { grid-column: 1 / -1; } }
.sr-only { position: absolute; width: 1px; height: 1px; margin: -1px; padding: 0; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.report-date-hidden { display: none; }
.account-hero-copy { min-width: 0; }
.account-hero-controls { display: flex; flex: none; align-items: flex-end; gap: 14px; }
.account-hero:has(.account-hero-controls)::after { display: none; }
.asof-control { display: flex; min-width: 0; flex-direction: column; }
.asof-control .asof-select { min-width: 210px; font-size: 14px; }
.account-hero + .grid { margin-top: 28px; }
.account-sticky { position: sticky; top: 66px; z-index: 40; display: grid; gap: 6px; margin-top: 31px; }
.account-sticky .account-nav { position: static; margin: 0; }
.section-jumps { display: flex; gap: 4px; overflow-x: auto; padding: 5px 7px; background: rgba(255, 255, 255, .9); border: 1px solid rgba(255, 255, 255, .85); border-radius: 13px; box-shadow: var(--shadow-sm); backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px); scrollbar-width: thin; }
.section-jumps a { flex: 0 0 auto; padding: 5px 11px; color: var(--muted-strong); border-radius: 999px; font-size: 12px; font-weight: 620; text-decoration: none; white-space: nowrap; }
.section-jumps a:hover, .section-jumps a:focus-visible { color: var(--ink); background: rgba(15, 23, 42, .05); }
.section-jumps a.active { color: var(--brand-deep); background: rgba(8, 120, 249, .10); }
.page-view > .card[id] { scroll-margin-top: 190px; }
.chart-head { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; }
.chart-mode { display: inline-flex; gap: 3px; padding: 3px; background: rgba(15, 23, 42, .045); border-radius: 10px; }
.chart-mode button { padding: 5px 11px; color: var(--muted-strong); background: transparent; border: 0; border-radius: 8px; cursor: pointer; font: inherit; font-size: 12px; font-weight: 650; }
.chart-mode button:hover { color: var(--ink); background: rgba(255, 255, 255, .7); }
.chart-mode button.active { color: #fff; background: var(--ink-soft); }
.chart-view .legend { margin-top: 10px; }
.page-error { margin-bottom: 16px; color: #9f1d14; background: rgba(180, 35, 24, .06); border-color: rgba(180, 35, 24, .22); }
.page-error a { margin-left: 6px; color: inherit; font-weight: 650; }
@media (max-width: 650px) {
  .account-hero-controls { flex-direction: column; align-items: stretch; }
  .asof-control .asof-select { min-width: 0; width: 100%; }
  .account-sticky { top: 0; margin-top: 24px; }
  .page-view > .card[id] { scroll-margin-top: 120px; }
}
/* System health and Administration: severity pills, incident cards and the agents' diagnoses. */
.ai-conversation-item .admin-chat-badge { display: inline-flex; margin-top: 0; color: #fff; font-size: 10px; }
.ops-pill { display: inline-flex; align-items: center; padding: 2px 9px; color: #334155; background: rgba(100, 116, 139, .14); border-radius: 999px; font-size: 11px; font-weight: 700; letter-spacing: .02em; vertical-align: middle; white-space: nowrap; }
.ops-pill.is-critical, .ops-pill.is-fail { color: #b4232f; background: rgba(217, 72, 95, .13); }
.ops-pill.is-warning, .ops-pill.is-warn { color: #8a5a00; background: rgba(217, 154, 21, .16); }
.ops-pill.is-pass { color: #1f7a4a; background: rgba(47, 158, 98, .14); }
.ops-pill.is-agent { color: #3d3aa8; background: rgba(103, 92, 255, .13); }
.health-overview-head { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; }
.health-overview-head .value { font-size: 26px; }
.health-incident { margin-top: 12px; padding: 16px; border: 1px solid var(--line); border-left: 4px solid #94a3b8; border-radius: var(--radius-md); }
.health-incident.is-critical { border-left-color: #d9485f; }
.health-incident.is-warning { border-left-color: #d99a15; }
.health-incident h3 { margin: 8px 0 4px; font-size: 16px; }
.agent-diagnosis { margin-top: 12px; padding: 13px 15px; background: linear-gradient(135deg, rgba(8, 120, 249, .06), rgba(103, 92, 255, .07)); border: 1px solid rgba(103, 92, 255, .2); border-radius: 13px; }
.agent-diagnosis p { margin: 6px 0; }
.agent-diagnosis ol { margin: 8px 0 0; padding-left: 20px; }
.agent-diagnosis .actions { margin-top: 12px; }
.admin-actions-panel #admin-confirmation { flex: 1 1 260px; min-width: 0; }
.admin-chat-who { margin-top: 14px; font-size: 12px; line-height: 1.5; }
@media (max-width: 650px) { #health-overview { grid-template-columns: 1fr 1fr; } }
@media screen {
  .ops-pill { color: #cbd5e1; background: rgba(148, 163, 184, .16); }
  .ops-pill.is-critical, .ops-pill.is-fail { color: #fda4af; }
  .ops-pill.is-warning, .ops-pill.is-warn { color: #fcd34d; }
  .ops-pill.is-pass { color: #86efac; }
  .ops-pill.is-agent { color: #c4b5fd; }
}
""" + DARK_THEME_CSS + MISSION_THEME_CSS
