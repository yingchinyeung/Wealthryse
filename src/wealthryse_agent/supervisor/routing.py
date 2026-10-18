"""The supervisor's routing rules for website chat: which team, which specialists, fast path or planner.

Rules first, as the design says; a question the rules can't place would go to the small router
model once the supervisor answers for real. In watch-only mode nothing here calls a model: a
question no rule places is recorded as undecided, and the shadow report counts how often.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import registry

# One pattern per client analysis specialist. A question can match several; then the planner splits it.
_RULES = {
    "holdings_performance": re.compile(
        r"\b(hold(s|ing|ings)?|positions?|own|owned|allocat\w*|sectors?|weights?|returns?|perform\w*|ytd|mtd|qtd|"
        r"year[- ]to[- ]date|month[- ]to[- ]date|since inception|gains?|loss(es)?|profits?|p&l|pnl|benchmark\w*|"
        r"outperform\w*|underperform\w*|nav|value(?! at risk)|worth|deposits?|deposited|withdraw\w*|cash|"
        r"dividends?|inflows?|outflows?|contribut\w*|movers?|top \d+|biggest|largest|live|intraday|right now|"
        r"today'?s?|(this|last) (week|month|quarter|year)|how (did|have|has|is|are) (i|we|my \w+|the portfolio|"
        r"the account|it) (do|done|doing|perform\w*|gone))\b", re.I),
    "risk": re.compile(
        r"\b(risk(s|y|ier|iest)?|volatil\w*|drawdowns?|sharpe|sortino|calmar|beta|alpha|value at risk|c?var|"
        r"expected shortfall|downside|tracking error|correlat\w*|regimes?|garch|outlook|forecast\w*|"
        r"factor exposures?|factors?|fama|information ratio)\b", re.I),
    "modelling": re.compile(
        r"\b(what[- ]if|simulat\w*|projections?|project(ed)?|monte carlo|retire\w*|"
        r"future value|in \d+ years|years from now|optimi[sz]\w*|efficient frontier|hypothetical\w*|"
        r"stress[- ]?test\w*|crash\w*|shocks?|scenarios?|what would (my|the|happen)|"
        r"if (the )?[\w&.-]+( [\w&.-]+)? (fell|falls|drops?|dropped|"
        r"rises?|rose|crash\w*|doubles?|halves?))\b", re.I),
    "metrics_guide": re.compile(
        r"(\bwhat( is|'s| are| does) (a |an |the )?(sharpe|sortino|calmar|twr|mwr|irr|time[- ]weighted|"
        r"money[- ]weighted|drawdown|volatility|beta|alpha|tracking error|c?var|expected shortfall|"
        r"information ratio|excess return)|\bdefin(e|ition|itions)\b|\bmean(s|ing)?\b|"
        r"\bhow (is|are|do you|was|were) ([\w-]+ ){0,3}(calculated|computed|worked out|measured)|"
        r"\bformula\b|\bmetrics guide\b|\bmethodology\b|\bexplain (the |this |that )?(metric|number|figure))",
        re.I),
}
# Words that place a question with holdings and performance only when no modelling rule matched:
# "project my value in 10 years" is a projection, not a valuation.
_WEAK_HOLDINGS = re.compile(r"\b(value(?! at risk)|worth|today'?s?|portfolio|account|returns?|hold(s|ing|ings)?|"
                            r"positions?|weights?|allocat\w*)\b", re.I)
# A definition asked about no one's account ("what is a Sharpe ratio?") needs only the guide.
_PERSONAL = re.compile(r"\b(my|our|i|me|we|mine|ours|this account|this portfolio)\b", re.I)
# Advice the analyst declines today; the live supervisor would send it to the same refusal.
_ADVICE = re.compile(r"\b(should (i|we)|recommend\w*|advice|advise|what (should i|to) (buy|sell)|"
                     r"tell me (what|which) to (buy|sell))\b", re.I)


@dataclass(frozen=True)
class Route:
    team: str
    specialists: tuple[str, ...]
    # rules: the rules placed it. router: no rule matched, so the router model would decide.
    decided_by: str
    advice: bool = False

    @property
    def fast_path(self) -> bool:
        """One specialist answers straight away, at today's cost; no planner, no extra checker."""
        return len(self.specialists) == 1

    @property
    def lead(self) -> str:
        """fast_path for one specialist, planner for several, router when no rule placed it."""
        if not self.specialists:
            return "router"
        return "fast_path" if self.fast_path else "planner"


def route_chat(question: str) -> Route:
    """Where the supervisor would send a website chat question about one account."""
    text = " ".join(str(question or "").split())
    matched = tuple(name for name in registry.CLIENT_SPECIALISTS if _RULES[name].search(text))
    if "modelling" in matched and "holdings_performance" in matched \
            and not _RULES["holdings_performance"].search(_WEAK_HOLDINGS.sub(" ", text)):
        matched = tuple(name for name in matched if name != "holdings_performance")
    if "metrics_guide" in matched and not _PERSONAL.search(text):
        matched = ("metrics_guide",)
    return Route(team="client_analysis", specialists=matched, decided_by="rules" if matched else "router",
                 advice=bool(_ADVICE.search(text)))


def agreement(planned: tuple[str, ...] | list[str], used: tuple[str, ...] | list[str]) -> str:
    """How the route compares with the specialists today's generalist actually needed.

    agree: the same. over: the route asked for more. under: it missed some. partial: some of each.
    differ: none in common. undecided: no rule placed it. no_tools: the answer needed no data.
    """
    planned_set, used_set = set(planned), set(used)
    if not used_set:
        return "agree" if not planned_set else "no_tools"
    if not planned_set:
        return "undecided"
    if planned_set == used_set:
        return "agree"
    if planned_set > used_set:
        return "over"
    if planned_set < used_set:
        return "under"
    return "partial" if planned_set & used_set else "differ"
