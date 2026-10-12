"""Website comparison versions, deliberately independent of report templates."""
from dataclasses import replace

LEGACY_COMPARISON_VERSION = 'benchmark-comparison-v1'
MS_HKD_COMPARISON_VERSION = 'benchmark-comparison-v2-ms-hkd'
T_HKD_COMPARISON_VERSION = 'benchmark-comparison-v2-t-hkd'


def website_comparison_version(subject_id: str, reporting_currency: str) -> str:
    if subject_id not in ('alpha-world-ms', 'alpha-world-t'):
        return LEGACY_COMPARISON_VERSION
    if reporting_currency != 'HKD':
        raise ValueError(f'{subject_id} website comparisons require HKD')
    return MS_HKD_COMPARISON_VERSION if subject_id == 'alpha-world-ms' else T_HKD_COMPARISON_VERSION


def require_t_website_benchmarks(specs) -> None:
    # Preserve T's recorded owner-approved roles. Its HKD correction does not
    # authorize copying MS's primary/secondary ordering or report benchmark.
    actual = [(s.benchmark_id, s.symbol, s.currency, s.reporting_currency,
               s.mapping_type, s.return_convention) for s in specs]
    expected = [('HSI', '^HSI', 'HKD', 'HKD', 'primary', 'price'),
                ('GSPC', '^GSPC', 'USD', 'HKD', 'secondary', 'price')]
    if len(actual) != 2 or sorted(actual) != sorted(expected):
        raise ValueError('T website requires exactly HSI primary and S&P 500 secondary in HKD')


def require_ms_website_benchmarks(specs) -> None:
    actual = [(s.benchmark_id, s.symbol, s.currency, s.reporting_currency,
               s.mapping_type, s.return_convention) for s in specs]
    expected = [('GSPC', '^GSPC', 'USD', 'HKD', 'primary', 'price'),
                ('HSI', '^HSI', 'HKD', 'HKD', 'secondary', 'price')]
    if len(actual) != 2 or sorted(actual) != sorted(expected):
        raise ValueError('MS website requires exactly S&P 500 primary and HSI secondary in HKD')


def version_website_comparison(result):
    return replace(result, calculation_version=website_comparison_version(
        result.subject_id, result.reporting_currency))
