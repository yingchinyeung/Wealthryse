"""Presentation-only risk labels. Never recalculates or fills missing metrics."""

RISK_DISPLAY_SCRIPT = r"""
const riskDisplayNotes = (risk, pct) => {
  const rm=risk.metrics||{}, evidence=risk.analytics_evidence||{}, unavailable=risk.unavailable_capabilities||{};
  const count=(...values)=>values.find(v=>typeof v==='number'&&Number.isInteger(v)&&v>=0);
  const text=v=>typeof v==='string'&&v.trim()?v.trim():'not recorded';
  const shown=v=>v===undefined?'unavailable':String(v);
  const returns=count(rm.return_observations,evidence.daily_return_observations);
  const valuations=count(rm.observations,evidence.nav_observations);
  const notes=[risk.message||'',`Validated return intervals: ${shown(returns)}; NAV valuations: ${shown(valuations)}. The first valuation is a starting point, not a return.`];
  if(typeof rm.daily_expected_tail_loss==='number'&&Number.isFinite(rm.daily_expected_tail_loss))
    notes.push('Daily ETL is historical 95% expected shortfall.');
  const paths=count(rm.drawdown_simulation_paths), horizon=count(rm.drawdown_horizon_sessions);
  if(!unavailable.one_year_drawdown&&paths>0&&horizon>0)
    notes.push(`One-year expected and tail maximum drawdown use ${paths} deterministic five-day moving-block bootstrap paths of ${horizon} sessions.`);
  else notes.push('One-year simulated drawdown is unavailable for this snapshot.');
  const cr=risk.crisis_replay;
  if(cr){
    notes.push(`Hypothetical fixed-unit crisis replay of holdings as of ${text(cr.holdings_as_of)}, from ${text(cr.crisis_start)} through ${text(cr.crisis_end)}; not actual portfolio performance during that period.`);
    notes.push(`Recorded components: ${shown(count(cr.direct_components))} direct and ${shown(count(cr.proxy_components))} proxies. Provider: ${text(cr.provider)}.`);
    if(typeof cr.cash_weight==='number'&&Number.isFinite(cr.cash_weight)) notes.push(`Cash weight: ${pct(cr.cash_weight)}, held flat; no rebalancing.`);
    if(cr.limitations) notes.push(String(cr.limitations));
    if(cr.provider_prices_independently_verified===false) notes.push('Historical provider prices have not been independently verified.');
    notes.push('Internal/shareholder analysis only; not for client distribution.');
  }else notes.push('Crisis drawdown is unavailable until the current-holdings replay and its proxy data pass validation.');
  notes.push(`Calculation: ${text(risk.runtime_version||risk.analytics_version)}.`);
  const rf=risk.risk_free;
  if(rf){
    notes.push(`Risk-free source: ${text(rf.provider)}; tenor: ${text(rf.tenor)}; latest fixing: ${text(rf.rate_date)}.`);
    if(rf.averaging_basis&&rf.period_start&&rf.period_end)
      notes.push(`Averaging period: ${text(rf.period_start)} through ${text(rf.period_end)}; basis: ${text(rf.averaging_basis)}.`);
  }
  const etl=risk.expected_tail_loss_contributors||{}, tails=count(etl.tail_observations);
  let attribution;
  if(unavailable.security_tail_attribution)
    attribution='Security-level ETL attribution is not qualified for this composite. No holding contributions or residual are shown.';
  else if(tails>0)
    attribution=`ETL contributors use unchanged carried USD positions on ${tails} portfolio tail days. Positive values added to loss; negative values offset loss. Residual from cash, fees, FX, trades and unmatched positions: ${pct(etl.residual_contribution)}.`;
  else attribution='Security-level ETL attribution is unavailable for this snapshot.';
  return {risk:notes.filter(Boolean).join(' '),attribution,
    attributionAvailable:!unavailable.security_tail_attribution&&tails>0};
};
"""
