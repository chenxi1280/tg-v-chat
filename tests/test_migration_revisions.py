import re
from pathlib import Path


ALEMBIC_VERSION_NUM_MAX_LENGTH = 32
MIGRATIONS = Path("migrations/versions")
REVISION_PATTERN = re.compile(r'^revision = "([^"]+)"$', re.MULTILINE)


def test_migration_revision_ids_fit_the_alembic_version_column():
    revisions = []
    for migration in MIGRATIONS.glob("*.py"):
        match = REVISION_PATTERN.search(migration.read_text(encoding="utf-8"))
        assert match is not None, f"missing revision id: {migration}"
        revisions.append(match.group(1))

    too_long = [revision for revision in revisions if len(revision) > ALEMBIC_VERSION_NUM_MAX_LENGTH]

    assert not too_long, f"revision ids exceed alembic_version.version_num: {too_long}"
