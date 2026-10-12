INSERT INTO benchmark_master(
    tenant_id, benchmark_id, symbol, name, provider, currency,
    return_convention, frequency, license_status, active, created_at, updated_at
)
VALUES (
    app_tenant_id(), 'XNDX', 'XNDX', 'Nasdaq-100 Total Return Index', NULL, 'USD',
    'gross_total_return', 'daily', 'pending', true,
    '2026-09-22T00:00:00+00:00', '2026-09-22T00:00:00+00:00'
)
ON CONFLICT (tenant_id, benchmark_id) DO NOTHING;

INSERT INTO portfolio_benchmark_mapping(
    tenant_id, subject_id, benchmark_id, mapping_type, effective_from,
    effective_to, reporting_currency, created_at
)
VALUES (
    app_tenant_id(), 'U16088571', 'XNDX', 'primary', '2025-01-20',
    NULL, 'USD', '2026-09-22T00:00:00+00:00'
)
ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from) DO NOTHING;

INSERT INTO metric_definitions(
    metric_id, version, display_name, description, formula_text,
    annualization_factor, created_at
)
VALUES
    ('total_return', 'benchmark-comparison-v1', 'Total return',
     'Compounded return over the aligned comparison period.',
     'ending_normalized_value / starting_normalized_value - 1', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('annualized_return', 'benchmark-comparison-v1', 'Annualized return',
     'Geometric return annualized by elapsed calendar days.',
     '(1 + total_return) ^ (365.25 / calendar_days) - 1', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('annualized_volatility', 'benchmark-comparison-v1', 'Annualized volatility',
     'Sample standard deviation of aligned periodic returns.',
     'sample_stdev(periodic_returns) * sqrt(252)', 252,
     '2026-09-22T00:00:00+00:00'),
    ('tracking_error', 'benchmark-comparison-v1', 'Tracking error',
     'Annualized sample standard deviation of portfolio minus benchmark returns.',
     'sample_stdev(portfolio_return - benchmark_return) * sqrt(252)', 252,
     '2026-09-22T00:00:00+00:00'),
    ('information_ratio', 'benchmark-comparison-v1', 'Information ratio',
     'Annualized mean excess return divided by tracking error.',
     'mean(excess_return) / sample_stdev(excess_return) * sqrt(252)', 252,
     '2026-09-22T00:00:00+00:00'),
    ('beta', 'benchmark-comparison-v1', 'Beta',
     'Sample covariance of portfolio and benchmark returns divided by benchmark variance.',
     'covariance(portfolio_return, benchmark_return) / variance(benchmark_return)', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('correlation', 'benchmark-comparison-v1', 'Correlation',
     'Pearson correlation of aligned portfolio and benchmark returns.',
     'correlation(portfolio_return, benchmark_return)', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('maximum_drawdown', 'benchmark-comparison-v1', 'Maximum drawdown',
     'Largest peak-to-trough decline on the compounded aligned return path.',
     'minimum(compounded_value / running_peak - 1)', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('relative_win_rate', 'benchmark-comparison-v1', 'Relative win rate',
     'Share of aligned periods in which the portfolio outperformed the benchmark.',
     'count(excess_return > 0) / count(excess_return)', NULL,
     '2026-09-22T00:00:00+00:00'),
    ('upside_capture', 'benchmark-comparison-v1', 'Upside capture',
     'Ratio of annualized compounded returns on positive benchmark periods.',
     'annualized_portfolio_up_period_return / annualized_benchmark_up_period_return', 252,
     '2026-09-22T00:00:00+00:00'),
    ('downside_capture', 'benchmark-comparison-v1', 'Downside capture',
     'Ratio of annualized compounded returns on negative benchmark periods.',
     'annualized_portfolio_down_period_return / annualized_benchmark_down_period_return', 252,
     '2026-09-22T00:00:00+00:00')
ON CONFLICT (metric_id, version) DO NOTHING;
