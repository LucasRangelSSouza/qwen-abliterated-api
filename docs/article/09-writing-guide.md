# Writing guide and pre-publication checklist

## Voice

First person, plain, specific. Say what you believed, what you measured, what changed. Prefer one exact number to three adjectives. The mistakes are the article's credibility: keep the wrong turns, cut the self-congratulation. Short paragraphs, one idea each.

Language: English for the Medium version (the `profissional` skill asks for English README and allows PT/EN articles). A Portuguese re-telling for LinkedIn should be a shorter version, not a translation: sections 1–4 and the results table.

## Structure rules

- Lead with the surprising fact (4.4 tok/s was the ceiling), not with the setup.
- Each section makes one claim, shows one piece of evidence (a figure, a table or a snippet) and ends with what a reader can do with it.
- A limitations section is mandatory and comes before the call to action.
- Captions state the source file and whether values are measured, published or theoretical.

## Tells to avoid (run the draft through the `humanize` skill afterwards)

- Staged openers ("In today's fast-moving world of AI…"), one-line dramatic closers, "not X but Y" contrasts used as rhetoric.
- Stock words and phrases: *delve*, *landscape*, *unlock*, *game-changing*, *seamless*, *robust*, *leverage*, "it's important to note".
- Forced triads and bold-label bullets in prose paragraphs.
- Inflated claims: "revolutionary", "10× faster" without the two numbers that make it 10×.
- Dashes used as a rhythm crutch.

## Figures

Use the five generated figures. They use a colour-blind-safe palette (Sonnet grey-blue, FP8 teal, NVFP4 orange, BF16 violet). Keep the captions' distinction between theoretical, published and measured. If a figure is redrawn in another tool, keep the CSV as the source of truth.

## Checklist before publishing

1. **Numbers.** Every number in the draft has a row in `06-claims-ledger.md` and matches `02-facts-and-numbers.md`. Rerun `python docs/article/build.py` if any benchmark was repeated.
2. **Claims.** Search the draft for "always", "proves", "faster than", "better than", "safe", "uncensored", "refuses": each needs a ledger row or a caveat. No statement about refusal behaviour beyond the publisher's model card.
3. **Sources.** Open every link marked *search summary* or *memory* in `05-sources.md`; confirm title, authors and the claim it supports. Check that links still resolve.
4. **Identifiers.** Remove or replace: the real domain and subdomain, IP addresses (edge host and GPU host), instance ids, mapped port numbers, account and key names, and provider price screenshots. Use `api.example.com`, `<edge-host>`, `<gpu-host>`, `<mapped-port>`.
5. **Secrets.** `git grep` the repository for the keys listed in the vault before making it public; confirm `terraform.tfvars`, `.tfstate` and `.env` are untracked; do not ship the edge SSH key as a secret of a public repository.
6. **Repository state.** The README quick start works from a clean clone; `tests/run_all.py` docstring lists the environment it needs; `docs/RESULTS.md` regenerated.
7. **Licences.** Mention the model licences and that the abliterated checkpoints are the publishers' research previews; do not imply endorsement.
8. **Responsible framing.** One short paragraph on what an unrestricted-model endpoint implies for whoever runs it (key protection, acceptable use). The article should not include prompts or outputs designed to demonstrate unsafe behaviour.

## Suggested publishing sequence

1. Repository public (after the checklist), README as the reproducible artefact.
2. Medium article, linking the repo and quoting only ledger-backed numbers.
3. LinkedIn post (150 words) pointing to the article.
4. CV bullet and interview narrative from `00-brief.md`.
5. Register the article and repo as done in the `profissional` skill's weekly checkpoint.
