import test from 'node:test';
import assert from 'node:assert/strict';
import {genericSummaryLabel, boundedGenericCommentary} from './report_summary_label.mjs';
const p={portfolio_total_return:.4035,excess_total_return:.178,portfolio_annualized_volatility:.2241,benchmark_annualized_volatility:.2203};
test('HK conclusion preserves direction and fits template budget',()=>{
  const s=genericSummaryLabel(p,'Hang Seng Price Index');
  assert.match(s,/Positive absolute return; ahead of Hang Seng Price Index/);
  assert.match(s,/Volatility is higher/);assert.ok(s.length<=240);
});
test('negative and flat results are not misrepresented',()=>{
  assert.match(genericSummaryLabel({...p,portfolio_total_return:-.1,excess_total_return:-.2,portfolio_annualized_volatility:.1},'S&P 500 Price Index'),/Negative.*behind.*lower/);
  assert.match(genericSummaryLabel({...p,portfolio_total_return:0,excess_total_return:0,portfolio_annualized_volatility:p.benchmark_annualized_volatility},'S&P 500 Price Index'),/Flat.*matching.*equal/);
});
test('missing metrics and oversized names fail closed',()=>{
  assert.throws(()=>genericSummaryLabel({...p,excess_total_return:undefined},'Index'));
  assert.throws(()=>genericSummaryLabel(p,'X'.repeat(65)));
  assert.ok(genericSummaryLabel(p,'X'.repeat(64)).length<=240);
});
test('optional narrative cannot overflow or insert unverified numbers',()=>{
  assert.equal(boundedGenericCommentary('Monitor concentration.'),'Monitor concentration.');
  for(const s of ['X'.repeat(241),'Return 10%','Two\nparagraphs'])assert.throws(()=>boundedGenericCommentary(s));
});
