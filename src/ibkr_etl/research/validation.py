"""Splits that keep tests honest: walk-forward and purged k-fold with an embargo.

* :func:`walk_forward_splits` – train on the past, test on the next block, roll forward.
  The test block never overlaps training, and an optional gap leaves space between them.
* :func:`purged_kfold` – k-fold cross-validation for labels that span time (for example a
  21-day forward return). Training observations whose label window overlaps the test fold
  are purged, and an embargo after each test fold drops observations whose features may
  still carry information from it (López de Prado, *Advances in Financial Machine Learning*,
  2018, chapter 7).

Indices refer to observations in time order. Pure functions.
"""
from __future__ import annotations

import math
from typing import Sequence


def walk_forward_splits(observations: int, *, train: int, test: int, step: int | None = None,
                        gap: int = 0, expanding: bool = False) -> list[tuple[list[int], list[int]]]:
    """(train indices, test indices) pairs rolling forward through ``observations``."""
    if min(train, test) <= 0 or gap < 0:
        raise ValueError("train and test must be positive and gap not negative")
    step = step or test
    splits = []
    start = 0
    while True:
        train_start = 0 if expanding else start
        train_end = start + train
        test_start = train_end + gap
        test_end = test_start + test
        if test_end > observations:
            break
        splits.append((list(range(train_start, train_end)), list(range(test_start, test_end))))
        start += step
    return splits


def purged_kfold(label_spans: Sequence[tuple[int, int]], *, folds: int = 5,
                 embargo: float = 0.01) -> list[tuple[list[int], list[int]]]:
    """(train, test) index lists with purging and an embargo.

    ``label_spans[i] = (first, last)`` gives the observations observation i's label depends
    on (inclusive), such as (i, i + 20) for a 21-day forward return. ``embargo`` is the share
    of all observations dropped from training right after each test fold.
    """
    count = len(label_spans)
    if folds < 2 or folds > count:
        raise ValueError("folds must be between 2 and the number of observations")
    for index, (first, last) in enumerate(label_spans):
        if not first <= last or first < index:
            raise ValueError(f"label span {index} must start at or after its observation and end after it starts")
    size = math.ceil(count * embargo)
    bounds = [round(count * k / folds) for k in range(folds + 1)]
    splits = []
    for k in range(folds):
        test = list(range(bounds[k], bounds[k + 1]))
        test_first = label_spans[test[0]][0]
        test_last = max(label_spans[i][1] for i in test)
        embargo_end = test[-1] + size
        train = []
        for i in range(count):
            if bounds[k] <= i < bounds[k + 1]:
                continue
            first, last = label_spans[i]
            overlaps = first <= test_last and last >= test_first
            embargoed = test[-1] < i <= embargo_end
            if not overlaps and not embargoed:
                train.append(i)
        splits.append((train, test))
    return splits
