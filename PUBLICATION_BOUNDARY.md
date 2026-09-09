# Publication boundary for maintainers

The public tree is an exact file allowlist, not a filtered copy of the private
laboratory repository. Adding a new evidence file requires a deliberate update
to `APPROVED_FILES` in `tools/check_public_boundary.py`.

Allowed categories:

- documentation written specifically for public disclosure;
- sanitized standard-tool output;
- screenshots that contain no hostnames, addresses or credentials;
- read-only state collectors;
- compute benchmarks;
- evidence checksums.

Forbidden categories:

- firmware, ROM, microcode, kernel modules or other binary payloads;
- code that writes PCI configuration space, MMIO/BAR registers or firmware;
- code that hooks, patches or changes a driver function or return value;
- driver unbind/rebind, module loading or service automation;
- exact proprietary write recipes;
- private infrastructure identifiers, credentials or access instructions;
- files copied wholesale from the private repository without a fresh review.

Before every public push:

1. run `python3 tools/check_public_boundary.py`;
2. review `git status --short`;
3. review the complete `git diff --cached`;
4. inspect every newly tracked file individually;
5. confirm that the commit contains no generated archive or binary other than
   the approved benchmark image;
6. push only after all checks pass.

Never use `git add .` in this repository. Stage explicit paths.
