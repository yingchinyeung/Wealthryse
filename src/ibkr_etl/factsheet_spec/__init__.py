"""Factsheet designs as data: a spec, a fixed vocabulary of bindings and components, and one engine.

A design spec says what to remove from an owner's template and what to draw in its
place, binding every figure to a Metrics guide definition. The engine renders it,
and checks generated from the spec read the PDF back before anyone sees it. Agents
draft and edit specs; they never write code.
"""
