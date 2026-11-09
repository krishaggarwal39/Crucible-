"""
Deployment-configuration invariants.

These are config-level guarantees that are easy to lose silently in a refactor and
that no unit test of application code would catch.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (ROOT / name).read_text()


# ── Uvicorn must not pre-empt the application's proxy policy ──────────────────

@pytest.mark.parametrize("path", ["Dockerfile", "docker-compose.prod.yml", "Makefile"])
def test_uvicorn_runs_with_no_proxy_headers(path):
    """
    Uvicorn's proxy_headers option defaults to ON and rewrites request.client from
    X-Forwarded-For before any application code runs. That silently overrides
    TRUSTED_PROXY_COUNT, and with --forwarded-allow-ips='*' uvicorn takes the
    leftmost (client-supplied) hop — a directly forgeable rate-limit identity.

    Verified empirically: with uvicorn's default handling, six requests from each
    of three forged IPs each got their own 10/min budget. With
    --no-proxy-headers, all eighteen shared one bucket.
    """
    content = _read(path)
    for line in content.splitlines():
        if "uvicorn" in line and "backend.main:app" in line:
            assert "--no-proxy-headers" in line, (
                f"{path} launches uvicorn without --no-proxy-headers: {line.strip()}"
            )


def test_forwarded_allow_ips_wildcard_is_never_used():
    """--forwarded-allow-ips='*' makes uvicorn trust the leftmost XFF hop."""
    for path in ["Dockerfile", "docker-compose.yml", "docker-compose.prod.yml", "Makefile"]:
        for line in _read(path).splitlines():
            # Skip comments — the files explain why this flag is avoided.
            if line.lstrip().startswith("#"):
                continue
            assert "forwarded-allow-ips" not in line, (
                f"{path} configures forwarded-allow-ips; the app owns this policy instead: "
                f"{line.strip()}"
            )


# ── Required secrets must not have insecure defaults in compose ───────────────

def _service_env(compose: dict, service: str) -> dict:
    return compose["services"][service].get("environment") or {}


@pytest.mark.parametrize("service", ["celery-worker", "celery-beat"])
def test_dev_compose_passes_required_secrets(service):
    """
    Both Celery services previously received neither JWT_SECRET_KEY nor
    CREDENTIAL_ENCRYPTION_KEY, so they crash-looped with a Pydantic validation
    error and `make up` produced no worker at all.
    """
    compose = yaml.safe_load(_read("docker-compose.yml"))
    env = _service_env(compose, service)
    for key in ("JWT_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY"):
        assert key in env, f"{service} is missing {key}"
        # `:?` makes compose fail fast with a message instead of starting broken.
        assert ":?" in str(env[key]), f"{service}.{key} should be declared required"


@pytest.mark.parametrize("service", ["backend", "celery-worker", "celery-beat"])
def test_prod_compose_passes_required_secrets(service):
    compose = yaml.safe_load(_read("docker-compose.prod.yml"))
    env = _service_env(compose, service)
    for key in ("JWT_SECRET_KEY", "CREDENTIAL_ENCRYPTION_KEY"):
        assert key in env, f"{service} is missing {key}"
        assert ":?" in str(env[key]), f"{service}.{key} should be declared required"


def test_compose_s3_credentials_match_minio_root():
    """
    The app's S3 credentials are derived from the MinIO root credentials so the
    server and client cannot default to different passwords (they previously
    defaulted to admin123 vs admin_secret, breaking every trace upload).
    """
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        content = _read(name)
        assert "S3_ACCESS_KEY: \"${MINIO_ROOT_USER" in content or \
               "S3_ACCESS_KEY: ${MINIO_ROOT_USER" in content, name
        assert "S3_SECRET_KEY: \"${MINIO_ROOT_PASSWORD" in content or \
               "S3_SECRET_KEY: ${MINIO_ROOT_PASSWORD" in content, name


def test_compose_uses_the_real_bucket_setting_name():
    """The application reads S3_BUCKET_TRACES, not S3_BUCKET_NAME."""
    for name in ("docker-compose.yml", "docker-compose.prod.yml"):
        content = _read(name)
        assert "S3_BUCKET_NAME" not in content, f"{name} uses a setting the app never reads"
        assert "S3_BUCKET_TRACES" in content


# ── Container hardening ───────────────────────────────────────────────────────

def test_backend_image_runs_as_non_root():
    content = _read("Dockerfile")
    assert re.search(r"^USER\s+\S+", content, re.MULTILINE), (
        "Dockerfile sets no USER, so the API and workers run as root"
    )


def test_backend_image_does_not_ship_tests():
    content = _read("Dockerfile")
    assert "COPY tests/" not in content, "test code should not be in the runtime image"


@pytest.mark.parametrize("path", [".dockerignore", "src/frontend/.dockerignore"])
def test_dockerignore_excludes_secrets_and_heavy_dirs(path):
    """
    Without these, `docker build` ships .env, .venv (~480MB) and node_modules into
    the build context.
    """
    content = _read(path)
    assert ".env" in content
    for entry in ("node_modules", ".next"):
        assert entry in content, f"{path} does not exclude {entry}"


def test_frontend_dockerfile_accepts_api_url_build_arg():
    """
    NEXT_PUBLIC_* is inlined at build time, so passing it only as a runtime env
    var (as prod compose used to) has no effect on the bundle.
    """
    content = _read("src/frontend/Dockerfile")
    assert "ARG NEXT_PUBLIC_API_URL" in content
    compose = yaml.safe_load(_read("docker-compose.prod.yml"))
    build = compose["services"]["frontend"]["build"]
    assert "NEXT_PUBLIC_API_URL" in (build.get("args") or {}), (
        "prod compose must pass NEXT_PUBLIC_API_URL as a build arg"
    )


# ── CI coverage ───────────────────────────────────────────────────────────────

def test_ci_runs_on_all_branches_and_covers_the_frontend():
    ci = yaml.safe_load(_read(".github/workflows/ci.yml"))
    # `on` is parsed as the boolean True by YAML 1.1.
    triggers = ci.get("on") or ci.get(True)
    assert triggers["push"]["branches"] == ["**"], "CI must run on every branch"
    assert "frontend" in ci["jobs"], "CI has no frontend job"
    steps = " ".join(
        str(s.get("run", "")) for s in ci["jobs"]["frontend"]["steps"]
    )
    for cmd in ("npm run lint", "npm run typecheck", "npm test", "npm run build"):
        assert cmd in steps, f"frontend CI job does not run `{cmd}`"


def test_ci_does_not_commit_an_encryption_key():
    """The workflow previously hardcoded a real Fernet key."""
    content = _read(".github/workflows/ci.yml")
    assert not re.search(r"CREDENTIAL_ENCRYPTION_KEY:\s*[A-Za-z0-9_\-]{40,}=", content), (
        "a literal Fernet key is committed in the CI workflow"
    )
    assert "Fernet.generate_key()" in content, "CI should generate an ephemeral key"


def test_ci_checks_for_migration_drift():
    """`alembic check` is what would have caught the agent_configs cascade drift."""
    content = _read(".github/workflows/ci.yml")
    assert "alembic check" in content
