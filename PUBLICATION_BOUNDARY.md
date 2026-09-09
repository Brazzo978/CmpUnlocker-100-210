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
- reviewed source and scripts required by the supported Tensor/Gen2 release;
- exact-firmware builders that distribute no proprietary firmware blob;
- bounded, fail-closed installers and oneshot units.

Forbidden categories:

- prebuilt firmware, ROM, microcode, kernel modules or other payload binaries;
- experimental write/retrain tools outside the reviewed Tensor/Gen2 path;
- Gen3 driver hooks, private-symbol offsets and unpublished bypass chains;
- private infrastructure identifiers, credentials or access instructions;
- files copied wholesale from the private repository without a fresh review.

Before every public push:

1. run `python3 tools/check_public_boundary.py`;
2. review `git status --short`;
3. review the complete `git diff --cached`;
4. inspect every newly tracked file individually;
5. confirm that the commit contains no generated archive or binary other than
   the approved benchmark image;
6. confirm that operational changes remain limited to the documented
   Tensor/Gen2 scope and retain all fail-closed checks;
7. push only after all checks pass.

Never use `git add .` in this repository. Stage explicit paths.
