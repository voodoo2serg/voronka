#!/usr/bin/env bash
# Run from a parent directory containing clean checkouts of the release candidates.
# Each Python checkout needs .venv from requirements-dev.txt. Install the locked
# npm dependencies in solo_pr and voronka/web, and install Playwright Chromium.
set -euo pipefail

root="$(cd "${1:-.}" && pwd -P)"
scratch="$(mktemp -d "$root/.signal-release.XXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT
for repo in voronka lms_end signal solo_pr openolat-solo-pr; do
    checkout="$root/$repo"
    test "$(git -C "$checkout" rev-parse --show-toplevel)" = "$checkout"
    if [ -n "$(git -C "$checkout" status --porcelain --untracked-files=no)" ]; then
        printf 'Tracked modifications in %s\n' "$repo" >&2
        exit 1
    fi
    printf 'release_sha %s %s\n' "$repo" "$(git -C "$checkout" rev-parse HEAD)"
done
for repo in voronka lms_end signal; do
    (
        cd "$root/$repo"
        export DATABASE_URL=sqlite:// REDIS_URL=redis://localhost:6379/0
        export MEDIA_ROOT="$scratch/media-$repo" SIGNAL_READY_REQUIRE_DB=false SIGNAL_READY_REQUIRE_REDIS=false
        if [ "$repo" = signal ]; then
            .venv/bin/ruff check apps tests
            (cd apps/api && ../../.venv/bin/python -m alembic heads)
        else
            .venv/bin/python -m alembic heads
        fi
        .venv/bin/python -m pytest -q --basetemp="$scratch/$repo"
    )
done
(
    cd "$root/voronka"
    PORTAL_BASE=/crm NEXT_PUBLIC_PORTAL=crm npm run --prefix web build
    DATABASE_URL=sqlite:// REDIS_URL=redis://localhost:6379/0 MEDIA_ROOT="$scratch/media-voronka" RUN_BROWSER=1 \
        .venv/bin/python -m pytest -q --basetemp="$scratch/voronka-browser" \
        tests/test_editor_browser.py tests/test_crm_browser.py tests/test_crm_square_constructor_ia_browser.py
    PORTAL_BASE=/ops NEXT_PUBLIC_PORTAL=ops npm run --prefix web build
    npm run --prefix web typecheck
)
(
    cd "$root/solo_pr"
    npm run build:vps
    npm test
    npm run test:worker
)
"$root/lms_end/scripts/prepare_openolat_chrome.sh" "$root/lms_end" "$root/openolat-solo-pr"
chrome="$root/lms_end/deploy/generated/openolat-chrome/current"
for asset in "$chrome"/*.js; do
    node --check "$asset"
done
printf 'openolat_overlay %s\n' "$(cat "$chrome/revision.txt")"
