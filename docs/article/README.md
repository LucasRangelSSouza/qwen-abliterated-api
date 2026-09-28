# Article dossier

Raw material for a future Medium article (and the LinkedIn/CV/interview derivatives) about this project. Nothing here is a finished article on purpose: it is the evidence, the structure and the guardrails, so that the writing can be done later, quickly, without re-deriving anything or overclaiming.

| file | what it is |
|---|---|
| [`00-brief.md`](00-brief.md) | thesis, audience, angle, title options, the opening hook, length and tone |
| [`01-outline.md`](01-outline.md) | section-by-section outline with the evidence and figure for each section |
| [`02-facts-and-numbers.md`](02-facts-and-numbers.md) | every number, with the file it came from (generated) |
| [`03-timeline-and-mistakes.md`](03-timeline-and-mistakes.md) | what happened in order, the wrong turns, and the lesson of each |
| [`figures/`](figures/) | five figures as PNG plus the CSV behind each (generated) |
| [`05-sources.md`](05-sources.md) | external sources, what each one supports, and how it was used |
| [`06-claims-ledger.md`](06-claims-ledger.md) | every claim the article may make, its evidence file, and what must not be claimed |
| [`07-snippets.md`](07-snippets.md) | short code excerpts worth quoting |
| [`08-glossary-and-faq.md`](08-glossary-and-faq.md) | definitions and the questions a reader will ask, with honest answers |
| [`09-writing-guide.md`](09-writing-guide.md) | voice, structure, style and the pre-publication checklist |
| [`build.py`](build.py) | regenerates the figures and the facts sheet from `reports/` |

## How to use it with the skills

- **`academico`**: technical-article/blog structure and citation discipline. Start from `05-sources.md` and `06-claims-ledger.md`; every citation there was actually opened or listed by a search, and each is marked with what it supports. Verify anything before quoting it verbatim.
- **`humanize`**: run the draft through it for voice and rhythm. `09-writing-guide.md` lists the tells to avoid before you start.
- **`relatorio`**: architecture diagram and a client-facing DOCX/PDF version. Source: `docs/ARCHITECTURE.md`, `docs/RESULTS.md`, `figures/`.
- **`profissional`**: this repo is registered there as Case 3 (AI platform / LLM serving), with the public/private checklist. The article is the "long-form" leg; the README is the "reproducible artefact" leg; the CV bullet and interview narrative are in `00-brief.md`.

## Regenerating the data

```bash
python docs/article/build.py        # figures + 02-facts-and-numbers.md from reports/*.json
python tests/make_results.py > docs/RESULTS.md
```

If you rerun a benchmark, rerun both commands and re-read `06-claims-ledger.md`: numbers in the prose must match the sheet.
