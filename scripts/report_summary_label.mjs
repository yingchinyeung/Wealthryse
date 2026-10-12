// Keep the generic template's conclusion within its existing text box.
// Quantitative details remain in the adjacent cards and tables.
export function genericSummaryLabel(primary, benchmarkName) {
  const fields = ['portfolio_total_return', 'excess_total_return',
    'portfolio_annualized_volatility', 'benchmark_annualized_volatility'];
  if (fields.some(k => typeof primary[k] !== 'number' || !Number.isFinite(primary[k]))) {
    throw new Error('Finite comparison metrics are required for the conclusion');
  }
  if (typeof benchmarkName !== 'string' || !benchmarkName.trim() || benchmarkName.length > 64) {
    throw new Error('A bounded benchmark name is required');
  }
  const sign = primary.portfolio_total_return > 0 ? 'Positive' : primary.portfolio_total_return < 0 ? 'Negative' : 'Flat';
  const relative = primary.excess_total_return > 0 ? 'ahead of' : primary.excess_total_return < 0 ? 'behind' : 'matching';
  const volatility = primary.portfolio_annualized_volatility > primary.benchmark_annualized_volatility ? 'higher' : primary.portfolio_annualized_volatility < primary.benchmark_annualized_volatility ? 'lower' : 'equal';
  const text = `${sign} absolute return; ${relative} ${benchmarkName}. Volatility is ${volatility} versus this benchmark. Monitor concentration, diversification and downside risk.`;
  if (text.length > 240) throw new Error('Conclusion exceeds the approved text budget');
  return text;
}

export function boundedGenericCommentary(text) {
  if (typeof text !== 'string' || text.length > 240 || /[0-9%$\r\n]/.test(text)) {
    throw new Error('Generic commentary exceeds the qualitative single-paragraph text budget');
  }
  return text;
}
