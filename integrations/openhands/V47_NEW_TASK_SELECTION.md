# v47 new instance selection (recorded before any gate run or model request)

Continued the frozen v40 candidate order after `django__django-15563` (used by v45/v46). Only
local SWE-bench Verified metadata, the patch **file list**, file **byte sizes**, and the host test
patch were read. The reference fix body and the public problem statement were **not** read for the
instances below, so no answer entered the selection.

Rules applied (unchanged from v40): difficulty `15 min - 1 hour` first, then `1-4 hours`;
`FAIL_TO_PASS >= 1`, `PASS_TO_PASS <= 150`; newest `created_at` first inside a tier; reference fix
touches 2–4 Python files; summed baseline bytes of those files `>= 50 KB` (weak proxy).

## Tier 1 (`15 min - 1 hour`) — exhausted under the byte proxy

| Candidate | Python files | Total bytes at base | Verdict |
|---|---:|---:|---|
| `django__django-15561` | 2 | 160,826 | **Gated and rejected** — see below |
| `django__django-15103` | 2 | 41,091 | Skip: below the 50 KB proxy |
| `django__django-14170` | 2 | 49,589 | Skip: below the 50 KB proxy |
| `django__django-12155` | 2 | 24,816 | Skip: below the 50 KB proxy |
| `django__django-11333` | 2 | 32,611 | Skip: below the 50 KB proxy |

No further tier-1 candidate has 2–4 Python files in its reference fix, so tier 1 is exhausted.

### `django__django-15561` gate result (FAIL)

Assembled the base checkout at `6991880109e35c879b71b7d9d9c154baeec12b89`, verified all five
touched files against the frozen git blob SHA-1 values, then applied the host test patch in two
isolated scoring copies. `test_alter_field_choices_noop` **passed on the unmodified base sources**
(`returncode=0`, `OK`), so the host target test cannot discriminate base from reference. The
`schema` module also has one pre-existing error (`test_func_unique_constraint_lookups`) that is
identical with and without the reference patch, i.e. environmental. Recorded as a gate failure;
the candidate is not used.

## Tier 2 (`1-4 hours`) — measured

| Candidate | Python files | Total bytes | FAIL labels usable | Verdict |
|---|---:|---:|---|---|
| `django__django-16631` | 2 | 12,711 | — | Skip: below proxy |
| `django__django-16560` | 2 | 25,934 | — | Skip: below proxy |
| `django__django-15957` | 1 | — | — | Skip: file-count rule |
| `django__django-15629` | 4 | 172,353 | **0/2** | Skip: FAIL_TO_PASS labels are human descriptions, not test IDs |
| `django__django-15503`, `-15268` | 1 | — | — | Skip: file-count rule |
| `django__django-14631` | 2 | 30,229 | — | Skip: below proxy |
| `django__django-14011` | 2 | 13,320 | — | Skip: below proxy |
| `django__django-14007`, `-13837`, `-13449`, `-13128`, `-12708` | 1 | — | — | Skip: file-count rule |
| **`django__django-13212`** | **2** | **66,457** | **5/5** | **Eligible — selected** |
| `django__django-11885` | 2 | 34,577 | — | Skip: below proxy |
| `django__django-11400` | 3 | 113,498 | 3/6 | Eligible but labels partly unusable; later in order |
| `django__django-11138` | 4 | 75,992 | 1/1 | Eligible; later in order |
| `django__django-10554` | 2 | 172,776 | 2/2 | Eligible; later in order |

## Selected instance: `django__django-13212`

- Base commit: `f4e93919e4608cfc50849a1f764fd856e0917401` (metadata `created_at` 2020-07-21).
- Reference fix files: `django/core/validators.py`, `django/forms/fields.py` (66,457 bytes total).
- Host test patch: `tests/forms_tests/tests/test_validators.py`, adding 5 tests, all with
  assertion lines; `FAIL_TO_PASS` = 5 usable labels, `PASS_TO_PASS` = 2 usable labels.
- Regression module for the runner: `forms_tests`.
- Rationale: first candidate in frozen order that passes every metadata rule **and** has fully
  machine-usable test labels, so the three-arm harness can drive it without inventing test IDs.

## Environment note (affects reproducibility of this selection)

Git's HTTPS is unusable inside the DSH sandbox (`schannel: AcquireCredentialsHandle failed:
SEC_E_NO_CREDENTIALS`), so `git clone`/lazy blob fetch fail. File bytes and blob contents were
therefore taken from `raw.githubusercontent.com` with Python `urllib`, and **every downloaded file
was verified against the git blob SHA-1 in the local object database** before use. The local Django
checkout is a blobless partial clone, which is why the SHA-1 comparison is the strongest available
integrity check.
