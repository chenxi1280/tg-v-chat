from pathlib import Path


def test_release_install_retry_reuses_uploaded_archive():
    script = Path("deploy/release.sh").read_text()

    assert "if [[ -f '${remote_tmp_archive}' ]]; then" in script
    assert "if [[ -f '${remote_archive}' ]]; then" in script
