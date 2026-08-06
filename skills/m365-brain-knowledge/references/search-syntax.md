# Search syntax

What `index search` accepts, and what each part does.

## Full-text queries

The query is parsed into an SQLite FTS5 expression. Terms are ANDed unless an
operator says otherwise.

| Query | Matches |
|---|---|
| `budget review` | both terms |
| `budget OR forecast` | either term |
| `budget NOT draft` | the first without the second |
| `"quarterly review"` | the exact phrase |
| `(budget OR forecast) AND 2026` | grouped |
| `-draft budget` | leading `-` is `NOT` |

Unbalanced parentheses and a leading bare `NOT` are rejected at parse time with
a message naming the query, rather than being silently repaired into a
different search.

Matching is per token. `budgets` does not match `budget`; there is no stemming.
When an exact term fails, reach for `--search-type hybrid`.

## Filters

Filters narrow; they never rank.

```
--type note              one entity type
--tag process            one tag
--field status=open      a frontmatter key (repeatable)
```

`--field` accepts `=`, `:`, `~`, `>`, `>=`, `<`, `<=`:

| Expression | Meaning |
|---|---|
| `status=open` | equals |
| `owner:alice` | equals (a readable alias for `=`) |
| `title~report` | contains |
| `priority>3` | numeric or lexicographic comparison |
| `due<=2026-12-31` | comparison |

Filters apply to `text` search. `vector` and `hybrid` reject the ones they
cannot honour, naming them — a filter silently ignored is a result set that
looks narrower than it is.

## Timeframes

`index recent --timeframe` accepts a compact form or a spelled one:

| Form | Meaning |
|---|---|
| `7d` | seven days |
| `12h` | twelve hours |
| `3w` | three weeks |
| `2m` | two months |
| `2 weeks ago` | two weeks |
| `1 day ago` | one day |

## Ranking

- **text** — BM25 over title, content and tags, weighted by
  `index.search.bm25_weights`. Higher is better.
- **vector** — cosine distance converted to a similarity, with everything below
  `index.search.min_similarity` discarded.
- **hybrid** — reciprocal-rank fusion of the two lists, with
  `index.search.rrf_k` as the fusion constant. Use it when the wording of the
  query is unlikely to match the wording of the document.

## Paging

`--page` is 1-based and `--limit` is the size of the page it walks, defaulting
to `index.search.page_size`. `--limit 5 --page 2` is therefore hits 6–10.

`--limit` used to trim a page the configured size had already capped, so a
limit above that size could not be reached: `--limit 100` against 23,012
matches returned 20, with nothing in the output saying the flag had been
ignored. The response now carries `total`, `returned` and `limit`, so a short
answer is visible as one.
