"""The coding-session handoff for a dossier (`artmind review prompt`).

A dossier's Agent prompt refers to its own Proposal and Synthetic example, so
a fresh coding session must read the dossier, not a pasted copy of one
section. This prompt names the dossier and the commands to read and check it,
and states the rules a run must keep: a new branch, the privacy preamble,
leakcheck before committing (false positives explained, not worked around),
no vault edits, no merge. It never contains the dossier's own content -- the
session reads that from the vault.
"""

from artmind.reviews.dossier import Dossier

TEMPLATE = """\
export ARTMIND_VAULT={vault}

Work on review dossier {review_id}. Read it with
`artmind review show {review_id} --markdown` (ARTMIND_VAULT must be set as above).

Do exactly the task in its "Agent prompt" section, on a new branch from {base}.
Follow the privacy preamble at the top of that section: the dossier is private
corpus data; nothing from it goes into code, tests, comments or commit messages
except the generic text in "Proposal" and the invented names in
"Synthetic example". Do not edit anything in the vault.

Before committing, run
`artmind review leakcheck {review_id} --base {base} --repo .`
and fix any hit (if a hit is a false positive, say why instead of rewording
around it). Finish with the report its last acceptance criterion asks for,
and do not merge.
"""


def coding_prompt(dossier: Dossier, *, base: str = "master") -> str:
    import paths

    vault = paths.ARTMIND_VAULT_DIR or "<path to the vault>"
    return TEMPLATE.format(vault=vault, review_id=dossier.review_id, base=base)
