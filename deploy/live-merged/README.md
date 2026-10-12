# Live website files merged with repo changes

`deploy/live-snapshot/` holds exact copies of four files taken from the image the
website runs (`IMAGE` names it), captured with Ying's approval on 2026-10-12.
The live site's versions of these files differ from the repo.

`deploy/live-merged/` holds the same files with only our holdings, performance
and cash-flow-adjusted returns changes merged in (three-way merge from the repo
baseline; conflicts resolved by keeping the live code and adding ours).
`deploy/website_release.py` releases these merged files instead of patching from
the repo baseline. Remove both folders once the repo matches what is live.
