# Present so pytest puts the repo root on sys.path and `import ledger_core`
# resolves against the working tree rather than an installed copy. Packaging
# comes later; until then this is what makes the suite runnable from a clone.
