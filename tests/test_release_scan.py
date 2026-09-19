"""Release scanner fails closed without echoing matched sensitive values."""

import hashlib
import subprocess

from scripts import release_scan


def test_blob_scan_reports_only_sanitized_categories_and_paths():
    private_path = "/" + "Users/example/private/input.db"
    secret_value = "highentropy" + "fixturevalue"
    secret_line = "client_" + f"secret = \"{secret_value}\""
    findings = release_scan._scan_blob(
        f"{private_path}\n{secret_line}\n".encode(),
        "synthetic/example.txt",
    )
    rendered = [item.as_dict() for item in findings]
    assert {item["category"] for item in rendered} == {
        "private_absolute_home_path",
        "literal_secret_assignment",
    }
    assert all(item["path"] == "synthetic/example.txt" for item in rendered)
    assert secret_value not in repr(rendered)
    assert private_path not in repr(rendered)


def test_safe_generic_examples_and_public_provenance_are_accepted():
    text = "\n".join((
        "PANEL_SECRET_KEY=<replace-with-random-value>",
        'PANEL_SECRET_KEY="$(openssl rand -hex 32)"',
        "support@example.invalid",
        "git@github.com:OWNER/Hermes-Health-Open-Source.git",
        "http://localhost:5111",
        "https://github.com/apache/echarts",
        "https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution",
        "https://files.pythonhosted.org/packages/reviewed/package-source.tar.gz",
        "https://fsf.org/",
        "https://www.gnu.org/licenses/",
        "https://api.open-meteo.com/v1/forecast",
        "198.51.100.77",
        "/var/lib/hermes/health.db",
    ))
    assert release_scan._scan_blob(text.encode(), ".env.example") == []


def test_private_network_address_is_rejected_but_value_is_not_rendered():
    private_address = "10.23." + "45.67"
    findings = release_scan._scan_blob(
        f"endpoint={private_address}\n".encode(),
        "synthetic/network.example",
    )
    assert [item.as_dict() for item in findings] == [{
        "category": "private_ipv4_address",
        "path": "synthetic/network.example",
    }]
    assert private_address not in repr(findings)


def test_removed_personal_fixture_term_is_rejected_without_echoing_it():
    removed_term = "butter" + " chicken"
    findings = release_scan._scan_blob(
        f"fixture={removed_term}\n".encode(),
        "synthetic/meal.txt",
    )
    assert [item.as_dict() for item in findings] == [{
        "category": "removed_personal_fixture_term",
        "path": "synthetic/meal.txt",
    }]
    assert removed_term not in repr(findings)


def test_path_classifier_rejects_data_secrets_caches_and_submodule_metadata():
    cases = {
        "data/health.db": "data_secret_or_generated_suffix",
        "cache/__pycache__/module.pyc": "generated_or_dependency_path",
        "training_seed.py": "forbidden_or_ambiguous_file",
        ".gitmodules": "forbidden_or_ambiguous_file",
        ".env": "live_environment_file",
    }
    for path, category in cases.items():
        assert category in {
            item.category for item in release_scan._path_findings(path)
        }


def test_reviewed_binary_requires_exact_bytes_and_repository_path(monkeypatch):
    path = "docs/assets/fictional.png"
    data = b"\x89PNG\r\n\x1a\nfictional review fixture"
    monkeypatch.setattr(release_scan, "REVIEWED_BINARY_ASSETS", {
        path: hashlib.sha256(data).hexdigest(),
    })
    assert release_scan._scan_blob(data, path) == []
    assert release_scan._scan_blob(
        data, f"history/{path}", repository_path=path,
    ) == []
    for changed in (data + b"changed", b"replacement text"):
        assert [item.category for item in release_scan._scan_blob(changed, path)] == [
            "reviewed_binary_digest_mismatch",
        ]
    for relocated in ("docs/assets/other.png", f"history/{path}"):
        assert [item.category for item in release_scan._scan_blob(data, relocated)] == [
            "unexplained_binary",
        ]


def test_history_checks_unapproved_path_even_for_same_reviewed_blob(tmp_path, monkeypatch):
    approved = "docs/assets/fictional.png"
    unapproved = "unreviewed.png"
    data = b"\x89PNG\r\n\x1a\nfictional review fixture"
    monkeypatch.setattr(release_scan, "REVIEWED_BINARY_ASSETS", {
        approved: hashlib.sha256(data).hexdigest(),
    })

    def git(*args):
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True,
        )

    git("init", "--quiet")
    git("config", "user.name", "Fictional Contributor")
    git("config", "user.email", "contributor@example.invalid")
    (tmp_path / approved).parent.mkdir(parents=True)
    (tmp_path / approved).write_bytes(data)
    (tmp_path / unapproved).write_bytes(data)
    git("add", approved, unapproved)
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Fictional assets")
    git("rm", "--quiet", unapproved)
    git("-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "Remove unreviewed copy")

    findings, blob_count = release_scan._history_scan(tmp_path)
    assert blob_count == 1
    assert [item.as_dict() for item in findings] == [{
        "category": "unexplained_binary", "path": f"history/{unapproved}",
    }]


def test_github_noreply_allowance_is_exact_and_only_for_commit_email_fields():
    owner = "@".join(("249340830+kajeesan", "users.noreply.github.com"))
    service = "@".join(("noreply", "github.com"))
    fields = ['a' * 40, 'Public Author', owner, 'GitHub', service, 'Reviewed change\n']
    assert release_scan._commit_metadata_findings('\n' + '\n'.join(fields)) == []
    rejected = (
        "@".join(("private-contact", "unapproved.test")),
        owner.replace('249340830', '249340831'),
        owner.replace('kajeesan', 'kajeesan-other'),
        owner + '.unapproved.test',
        owner + '+extra',
        'prefix ' + owner,
        service.replace('noreply', 'no-reply'),
    )
    for email in rejected:
        for index in (2, 4):
            changed = fields.copy()
            changed[index] = email
            findings = release_scan._commit_metadata_findings('\n'.join(changed))
            assert [item.as_dict() for item in findings] == [{
                'category': 'non_example_email', 'path': 'history/<commit-metadata>',
            }]
            assert email not in repr([item.as_dict() for item in findings])
    for email in (owner, service):
        for index in (1, 3, 5):
            changed = fields.copy()
            changed[index] = email
            assert 'non_example_email' in {
                item.category for item in release_scan._commit_metadata_findings('\n'.join(changed))
            }
        for path in ('README.md', 'history/<references>'):
            assert [item.category for item in release_scan._text_findings(email, path)] == ['non_example_email']
    changed = fields.copy()
    changed[5] = '/' + 'Users/fictional/private-file'
    assert [item.category for item in release_scan._commit_metadata_findings('\n'.join(changed))] == ['private_absolute_home_path']


def test_actual_history_accepts_only_reviewed_github_fields(tmp_path):
    owner = "@".join(("249340830+kajeesan", "users.noreply.github.com"))
    service = "@".join(("noreply", "github.com"))

    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)

    git('init', '--quiet')
    git('config', 'user.name', 'GitHub')
    git('config', 'user.email', service)
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet',
        '--author', f'Public Author <{owner}>', '-m', 'Reviewed merge')
    assert release_scan._history_scan(tmp_path) == ([], 0)
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet',
        '--author', f'Public Author <{owner}>', '-m', service)
    findings, blobs = release_scan._history_scan(tmp_path)
    assert blobs == 0
    assert [item.as_dict() for item in findings] == [{
        'category': 'non_example_email', 'path': 'history/<commit-metadata>',
    }]
