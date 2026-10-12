"""Exact v5 scan with the previously qualified ARM/CLI default normalization."""
import sys
import release_reporting_history_20261011 as candidate
import scan_reporting_full_nav_20261011 as previous
previous.release.OUT = candidate.OUT
previous.scan.jobs_unchanged = previous.jobs_unchanged
if __name__ == '__main__':
    if len(sys.argv) != 1:
        raise ValueError('Exact v5 candidate only')
    sys.argv = [sys.argv[0], '--folder', str(candidate.OUT)]
    previous.scan.main()
