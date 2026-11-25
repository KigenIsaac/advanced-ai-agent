"""Validation shared by engineering design tools."""

import math


def mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def items(value, label, maximum=250):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be an array of up to {maximum} entries")
    return value


def names(value, label):
    values = items(value, label)
    if any(not isinstance(v, str) or not v.strip() for v in values) or len(
        set(values)
    ) != len(values):
        raise ValueError(f"{label} requires unique nonblank names")
    return values


def number(value, label, minimum=0):
    if (
        type(value) not in (float, int)
        or abs(value) > 1e18
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{label} must be finite and at least {minimum}")
    return value


def positive(value, label):
    n = number(value, label)
    if n == 0:
        raise ValueError(f"{label} must be positive")
    return n
