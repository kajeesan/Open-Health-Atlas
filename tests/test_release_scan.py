"""Release scanner fails closed without echoing matched sensitive values."""

import hashlib
import subprocess

import pytest

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
        "https://core.telegram.org/bots/features#creating-a-new-bot",
        "https://developers.google.com/health/setup",
        "https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/",
        "https://hevy.com/settings?developer",
        "https://www.hostinger.com/support/5726606-how-to-use-the-vps-dashboard-in-hostinger/",
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


def _dependabot_metadata_fields():
    address = "@".join(("support", "github.com"))
    return [
        'a' * 40, 'dependabot[bot]',
        "@".join(("49699333+dependabot[bot]", "users.noreply.github.com")),
        'GitHub', "@".join(("noreply", "github.com")),
        f'Bump a fictional dependency.\n\nSigned-off-by: dependabot[bot] <{address}>',
    ]


@pytest.mark.parametrize('ending', ['', '\n'], ids=['git-log-no-final-lf', 'final-lf'])
def test_dependabot_public_signoff_is_accepted_for_canonical_author(ending):
    fields = _dependabot_metadata_fields()
    fields[5] += ending

    assert release_scan._commit_metadata_findings('\n'.join(fields)) == []


@pytest.mark.parametrize(('field', 'before', 'after'), [
    pytest.param(1, 'dependabot[bot]', 'Public Contributor', id='wrong-author'),
    pytest.param(2, '49699333', '49699334', id='wrong-account-id'),
    pytest.param(2, 'users.noreply.github.com', 'unapproved.test', id='wrong-author-domain'),
    pytest.param(2, 'dependabot', 'Dependabot', id='author-case'),
    pytest.param(5, '\n\nSigned-off-by:', '\nSigned-off-by:', id='missing-blank-boundary'),
    pytest.param(5, 'Signed-off-by:', 'Signed-Off-By:', id='changed-label'),
    pytest.param(5, 'Signed-off-by:', ' Signed-off-by:', id='indented-footer'),
    pytest.param(5, 'dependabot[bot]', 'another[bot]', id='wrong-signatory'),
    pytest.param(5, 'support', 'Support', id='changed-address-case'),
    pytest.param(5, 'github.com', 'github.com.unapproved.test', id='address-suffix'),
    pytest.param(5, '>', '> ', id='trailing-space'),
    pytest.param(5, '>', '>\nLater content', id='nonterminal-footer'),
    pytest.param(5, '>', '>\r\n', id='unreviewed-crlf'),
])
def test_dependabot_signoff_lookalikes_remain_rejected(field, before, after):
    fields = _dependabot_metadata_fields()
    fields[field] = fields[field].replace(before, after)

    findings = release_scan._commit_metadata_findings('\n'.join(fields))

    assert 'non_example_email' in {item.category for item in findings}
    assert "@".join(("support", "github.com")) not in repr(findings)


@pytest.mark.parametrize(('prefix', 'category'), [
    pytest.param("@".join(("support", "github.com")), 'non_example_email', id='address-in-body'),
    pytest.param('Signed-off-by: dependabot[bot] <' + "@".join(("support", "github.com")) + '>',
                 'non_example_email', id='duplicate-earlier-footer'),
    pytest.param('/' + 'Users/fictional/private-records', 'private_absolute_home_path', id='private-path'),
    pytest.param('client_' + 'secret = "highentropyfixturevalue"', 'literal_secret_assignment', id='secret'),
])
def test_dependabot_footer_does_not_hide_other_message_findings(prefix, category):
    fields = _dependabot_metadata_fields()
    fields[5] = prefix + '\n\n' + fields[5]

    findings = release_scan._commit_metadata_findings('\n'.join(fields))

    assert {item.category for item in findings} == {category}
    assert prefix not in repr(findings)


@pytest.mark.parametrize('field', [1, 3, 4], ids=['author-name', 'committer-name', 'committer-email'])
def test_dependabot_footer_does_not_allow_address_in_other_metadata(field):
    fields = _dependabot_metadata_fields()
    fields[field] = "@".join(("support", "github.com"))

    findings = release_scan._commit_metadata_findings('\n'.join(fields))

    assert 'non_example_email' in {item.category for item in findings}


@pytest.mark.parametrize('path', ['README.md', 'history/<references>'])
def test_dependabot_signoff_remains_rejected_outside_commit_messages(path):
    body = _dependabot_metadata_fields()[5]

    assert [item.category for item in release_scan._text_findings(body, path)] == ['non_example_email']


def test_actual_dependabot_history_keeps_other_message_emails_private(tmp_path):
    fields = _dependabot_metadata_fields()

    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)

    git('init', '--quiet')
    git('config', 'user.name', fields[3])
    git('config', 'user.email', fields[4])
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet',
        '--author', f'{fields[1]} <{fields[2]}>', '-m', fields[5])
    assert release_scan._history_scan(tmp_path) == ([], 0)

    private_email = "@".join(("private-contact", "unapproved.test"))
    git('-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '--quiet',
        '--author', f'{fields[1]} <{fields[2]}>', '-m', private_email + '\n\n' + fields[5])
    findings, blobs = release_scan._history_scan(tmp_path)

    assert blobs == 0
    assert [item.as_dict() for item in findings] == [{
        'category': 'non_example_email', 'path': 'history/<commit-metadata>',
    }]
    assert private_email not in repr(findings)
