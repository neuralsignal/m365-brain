"""The vault: where every path in it comes from, and how things leave it.

`paths` builds them, `classify` judges the untrusted ones, `removal` deletes.
Nothing here knows about Microsoft 365 -- the vault is a folder contract, and
the extractors are one writer of it.
"""
