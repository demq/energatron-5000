---
name: new-doc
description: Create a new dated document in agent_docs/YYYY-MM/YYYY-MM-DD_<name>.md in the right repo. Use when asked to write a new spec, design doc, note or report.
---

# new-doc

1. Pick the repo:
   - design docs, specs and anything free of personal data → `energatron-5000/agent_docs/`
   - anything that mentions the owner's data, identifiers or figures → `demq-elektric/agent_docs/`
2. Name it `agent_docs/<YYYY-MM>/<YYYY-MM-DD>_<name>.md`, with today's date and a short kebab-case `<name>`.
   Create the month folder if needed. If the name is taken, choose a more specific one; never overwrite or
   rename an existing doc.
3. Start the file with:

   ```markdown
   # <Title>

   - **Created:** <YYYY-MM-DD>
   - **Status:** Draft
   - **Privacy note:** <for the tool repo: "contains no identifiers or usage figures, and nothing added to it may">
   ```
4. In the tool repo, run the privacy scan from `CLAUDE.md` on the new file before reporting.
5. Report the path.
