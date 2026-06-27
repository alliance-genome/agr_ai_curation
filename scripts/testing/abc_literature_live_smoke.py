#!/usr/bin/env python3
"""Run the durable ABC Literature live smoke with ephemeral Cognito users."""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence


DEFAULT_BASE_URL = "https://stage-literature-rest.alliancegenome.org"
DEFAULT_AWS_REGION = "us-east-1"
DEFAULT_USER_POOL_ID = "us-east-1_d3eK6SYpI"
DEFAULT_CLIENT_ID = ""
DEFAULT_AUTHORIZED_GROUPS = ("FBStaff", "FlyBaseCurator")
DEFAULT_UNKNOWN_MD5 = "0" * 32
DEFAULT_KNOWN_MD5 = "000c0dd769dd7326" + "8e3c752102337c96"
DEFAULT_RESTRICTED_MD5 = DEFAULT_KNOWN_MD5
DEFAULT_PMID = "23970418"
DEFAULT_REFERENCE = "AGRKB:101000000055784"
DEFAULT_SOURCE_REFERENCEFILE_ID = "4040596"
DEFAULT_CONVERTED_REFERENCEFILE_ID = "4672234"
DEFAULT_TIMEOUT_SECONDS = 20.0
DEFAULT_PYTEST_TIMEOUT_SECONDS = 180.0
DEFAULT_AWS_API_TIMEOUT_SECONDS = 30.0
DEFAULT_EVIDENCE_TAIL_LIMIT = 4000
DEFAULT_EVIDENCE_DIR = Path("file_outputs/temp")


class SmokeFailure(RuntimeError):
    """Raised when the smoke runner cannot complete safely."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SmokeUser:
    username: str
    email: str
    groups: tuple[str, ...]


@dataclass(frozen=True)
class SmokeConfig:
    repo_root: Path
    aws_profile: str | None
    region: str
    user_pool_id: str
    client_id: str
    client_secret: str | None
    base_url: str
    authorized_groups: tuple[str, ...]
    evidence_dir: Path
    pytest_timeout_seconds: float
    literature_timeout_seconds: float
    aws_api_timeout_seconds: float
    evidence_tail_limit: int
    keep_users: bool
    user_prefix: str
    unknown_md5: str
    known_md5: str
    restricted_md5: str
    pmid: str
    reference: str
    source_referencefile_id: str
    converted_referencefile_id: str
    python_executable: str
    pytest_args: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class SmokeRunResult:
    exit_code: int
    evidence_path: Path
    evidence: dict[str, Any]


PytestRunner = Callable[[SmokeConfig, dict[str, str], Sequence[str]], CommandResult]


class AwsSmokeClient(Protocol):
    def caller_identity(self) -> dict[str, Any]:
        ...

    def discover_client_secret(self) -> str | None:
        ...

    def create_user(self, *, username: str, email: str) -> None:
        ...

    def set_user_password(self, *, username: str, password: str) -> None:
        ...

    def add_user_to_group(self, *, username: str, group: str) -> None:
        ...

    def initiate_auth(self, *, username: str, auth_parameters: dict[str, str]) -> dict[str, Any]:
        ...

    def delete_user(self, *, username: str) -> None:
        ...


AwsClientFactory = Callable[[SmokeConfig], AwsSmokeClient]


class Boto3AwsSmokeClient:
    def __init__(self, config: SmokeConfig) -> None:
        try:
            import boto3
            from botocore.config import Config
        except Exception as exc:  # pragma: no cover - exercised only without boto3
            raise SmokeFailure(
                "boto3/botocore are required for the ABC Literature live smoke runner"
            ) from exc

        session_kwargs: dict[str, str] = {"region_name": config.region}
        if config.aws_profile:
            session_kwargs["profile_name"] = config.aws_profile
        session = boto3.Session(**session_kwargs)
        timeout_config = Config(
            connect_timeout=min(10.0, config.aws_api_timeout_seconds),
            read_timeout=config.aws_api_timeout_seconds,
            retries={"max_attempts": 2, "mode": "standard"},
        )
        self._config = config
        self._sts = session.client("sts", config=timeout_config)
        self._cognito = session.client("cognito-idp", config=timeout_config)

    def caller_identity(self) -> dict[str, Any]:
        return dict(self._sts.get_caller_identity())

    def discover_client_secret(self) -> str | None:
        payload = self._cognito.describe_user_pool_client(
            UserPoolId=self._config.user_pool_id,
            ClientId=self._config.client_id,
        )
        secret = (payload.get("UserPoolClient") or {}).get("ClientSecret")
        return secret if isinstance(secret, str) and secret.strip() else None

    def create_user(self, *, username: str, email: str) -> None:
        self._cognito.admin_create_user(
            UserPoolId=self._config.user_pool_id,
            Username=username,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
        )

    def set_user_password(self, *, username: str, password: str) -> None:
        self._cognito.admin_set_user_password(
            UserPoolId=self._config.user_pool_id,
            Username=username,
            Password=password,
            Permanent=True,
        )

    def add_user_to_group(self, *, username: str, group: str) -> None:
        self._cognito.admin_add_user_to_group(
            UserPoolId=self._config.user_pool_id,
            Username=username,
            GroupName=group,
        )

    def initiate_auth(self, *, username: str, auth_parameters: dict[str, str]) -> dict[str, Any]:
        return dict(
            self._cognito.admin_initiate_auth(
                UserPoolId=self._config.user_pool_id,
                ClientId=self._config.client_id,
                AuthFlow="ADMIN_NO_SRP_AUTH",
                AuthParameters=auth_parameters,
            )
        )

    def delete_user(self, *, username: str) -> None:
        self._cognito.admin_delete_user(
            UserPoolId=self._config.user_pool_id,
            Username=username,
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp_for_file(now: datetime) -> str:
    return now.strftime("%Y%m%dT%H%M%SZ")


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _parse_groups(value: str) -> tuple[str, ...]:
    groups = tuple(group.strip() for group in value.split(",") if group.strip())
    if not groups:
        raise SmokeFailure("At least one authorized Cognito group is required")
    return groups


def _repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    default_aws_profile = _env_first(
        "ABC_LITERATURE_SMOKE_AWS_PROFILE",
        "AWS_PROFILE",
        default="ctabone",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Create ephemeral Cognito smoke users, run the ABC Literature live "
            "pytest harness, delete the users, and write non-secret evidence JSON."
        )
    )
    parser.add_argument(
        "--aws-profile",
        default=default_aws_profile,
        help=(
            "AWS profile with Cognito admin permissions. Defaults to "
            "ABC_LITERATURE_SMOKE_AWS_PROFILE, AWS_PROFILE, then ctabone."
        ),
    )
    parser.add_argument(
        "--region",
        default=_env_first("ABC_LITERATURE_SMOKE_AWS_REGION", default=DEFAULT_AWS_REGION),
    )
    parser.add_argument(
        "--user-pool-id",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_USER_POOL_ID",
            default=DEFAULT_USER_POOL_ID,
        ),
    )
    parser.add_argument(
        "--client-id",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_CLIENT_ID",
            default=DEFAULT_CLIENT_ID,
        ),
    )
    parser.add_argument(
        "--client-secret",
        default=_env_first("ABC_LITERATURE_SMOKE_CLIENT_SECRET", default=""),
        help="Optional Cognito app client secret. Never written to evidence.",
    )
    parser.add_argument(
        "--base-url",
        default=_env_first(
            "ABC_LITERATURE_LIVE_BASE_URL",
            "ABC_LITERATURE_SMOKE_BASE_URL",
            default=DEFAULT_BASE_URL,
        ),
    )
    parser.add_argument(
        "--authorized-groups",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_AUTHORIZED_GROUPS",
            default=",".join(DEFAULT_AUTHORIZED_GROUPS),
        ),
        help="Comma-separated Cognito groups for the authorized smoke user.",
    )
    parser.add_argument(
        "--evidence-dir",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_EVIDENCE_DIR",
            default=str(DEFAULT_EVIDENCE_DIR),
        ),
    )
    parser.add_argument(
        "--pytest-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ABC_LITERATURE_SMOKE_PYTEST_TIMEOUT_SECONDS",
                default=str(DEFAULT_PYTEST_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--literature-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ABC_LITERATURE_LIVE_TIMEOUT_SECONDS",
                "ABC_LITERATURE_SMOKE_TIMEOUT_SECONDS",
                default=str(DEFAULT_TIMEOUT_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--aws-api-timeout-seconds",
        type=float,
        default=float(
            _env_first(
                "ABC_LITERATURE_SMOKE_AWS_API_TIMEOUT_SECONDS",
                default=str(DEFAULT_AWS_API_TIMEOUT_SECONDS),
            )
        ),
        help="Botocore read timeout for Cognito/STS calls.",
    )
    parser.add_argument(
        "--evidence-tail-limit",
        type=int,
        default=int(
            _env_first(
                "ABC_LITERATURE_SMOKE_EVIDENCE_TAIL_LIMIT",
                default=str(DEFAULT_EVIDENCE_TAIL_LIMIT),
            )
        ),
        help="Maximum stdout/stderr characters stored per pytest evidence tail.",
    )
    parser.add_argument(
        "--keep-users",
        action="store_true",
        help="Debug only: leave ephemeral Cognito users in place after the run.",
    )
    parser.add_argument(
        "--user-prefix",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_USER_PREFIX",
            default="ai-curation-live-smoke",
        ),
    )
    parser.add_argument(
        "--unknown-md5",
        default=_env_first(
            "ABC_LITERATURE_LIVE_UNKNOWN_MD5",
            "ABC_LITERATURE_SMOKE_UNKNOWN_MD5",
            default=DEFAULT_UNKNOWN_MD5,
        ),
    )
    parser.add_argument(
        "--known-md5",
        default=_env_first(
            "ABC_LITERATURE_LIVE_KNOWN_MD5",
            "ABC_LITERATURE_SMOKE_KNOWN_MD5",
            default=DEFAULT_KNOWN_MD5,
        ),
    )
    parser.add_argument(
        "--restricted-md5",
        default=_env_first(
            "ABC_LITERATURE_LIVE_RESTRICTED_MD5",
            "ABC_LITERATURE_SMOKE_RESTRICTED_MD5",
            default=DEFAULT_RESTRICTED_MD5,
        ),
    )
    parser.add_argument(
        "--pmid",
        default=_env_first(
            "ABC_LITERATURE_LIVE_PMID",
            "ABC_LITERATURE_SMOKE_PMID",
            default=DEFAULT_PMID,
        ),
    )
    parser.add_argument(
        "--reference",
        default=_env_first(
            "ABC_LITERATURE_LIVE_REFERENCE",
            "ABC_LITERATURE_SMOKE_REFERENCE",
            default=DEFAULT_REFERENCE,
        ),
    )
    parser.add_argument(
        "--source-referencefile-id",
        default=_env_first(
            "ABC_LITERATURE_SMOKE_SOURCE_REFERENCEFILE_ID",
            default=DEFAULT_SOURCE_REFERENCEFILE_ID,
        ),
        help="Recorded in evidence for the known source PDF fixture.",
    )
    parser.add_argument(
        "--converted-referencefile-id",
        default=_env_first(
            "ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID",
            "ABC_LITERATURE_SMOKE_CONVERTED_REFERENCEFILE_ID",
            default=DEFAULT_CONVERTED_REFERENCEFILE_ID,
        ),
    )
    parser.add_argument(
        "--python-executable",
        default=_env_first("PYTHON", default=sys.executable),
    )
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="Additional argument appended to the pytest invocation. Repeatable.",
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> SmokeConfig:
    repo_root = _repo_root_from_script()
    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_absolute():
        evidence_dir = repo_root / evidence_dir
    return SmokeConfig(
        repo_root=repo_root,
        aws_profile=args.aws_profile.strip() or None,
        region=args.region,
        user_pool_id=args.user_pool_id,
        client_id=args.client_id,
        client_secret=args.client_secret or None,
        base_url=args.base_url.rstrip("/"),
        authorized_groups=_parse_groups(args.authorized_groups),
        evidence_dir=evidence_dir,
        pytest_timeout_seconds=args.pytest_timeout_seconds,
        literature_timeout_seconds=args.literature_timeout_seconds,
        aws_api_timeout_seconds=args.aws_api_timeout_seconds,
        evidence_tail_limit=args.evidence_tail_limit,
        keep_users=args.keep_users,
        user_prefix=args.user_prefix.strip(),
        unknown_md5=args.unknown_md5,
        known_md5=args.known_md5,
        restricted_md5=args.restricted_md5,
        pmid=args.pmid,
        reference=args.reference,
        source_referencefile_id=args.source_referencefile_id,
        converted_referencefile_id=args.converted_referencefile_id,
        python_executable=args.python_executable,
        pytest_args=tuple(args.pytest_arg or ()),
    )


def redact_text(value: str, secret_values: Iterable[str]) -> str:
    redacted = value
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!#$_%+="
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(32))
        if (
            any(char.islower() for char in password)
            and any(char.isupper() for char in password)
            and any(char.isdigit() for char in password)
            and any(char in "!#$_%+=" for char in password)
        ):
            return password


def cognito_secret_hash(username: str, client_id: str, client_secret: str) -> str:
    digest = hmac.new(
        client_secret.encode("utf-8"),
        msg=(username + client_id).encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def create_smoke_user(
    config: SmokeConfig,
    *,
    username: str,
    groups: tuple[str, ...],
    aws_client: AwsSmokeClient,
) -> tuple[SmokeUser, str]:
    email = username if "@" in username else f"{username}@example.invalid"
    password = generate_password()
    user = SmokeUser(username=username, email=email, groups=groups)
    created = False
    try:
        aws_client.create_user(username=username, email=email)
        created = True
        aws_client.set_user_password(username=username, password=password)
        for group in groups:
            aws_client.add_user_to_group(username=username, group=group)
        return user, password
    except Exception as exc:
        if created:
            try:
                delete_smoke_user(user, aws_client)
            except Exception as cleanup_exc:
                raise SmokeFailure(
                    f"{exc}; cleanup of partially created user {username} failed: "
                    f"{cleanup_exc}"
                ) from exc
        raise


def token_for_user(
    config: SmokeConfig,
    *,
    username: str,
    password: str,
    aws_client: AwsSmokeClient,
) -> str:
    auth_parameters = {
        "USERNAME": username,
        "PASSWORD": password,
    }
    if config.client_secret:
        auth_parameters["SECRET_HASH"] = cognito_secret_hash(
            username,
            config.client_id,
            config.client_secret,
        )
    payload = aws_client.initiate_auth(username=username, auth_parameters=auth_parameters)
    token = (
        payload.get("AuthenticationResult", {})
        if isinstance(payload, dict)
        else {}
    ).get("IdToken")
    if not isinstance(token, str) or not token.strip():
        raise SmokeFailure(f"Cognito auth for {username} did not return an IdToken")
    return token


def delete_smoke_user(
    user: SmokeUser,
    aws_client: AwsSmokeClient,
) -> None:
    aws_client.delete_user(username=user.username)


def build_pytest_env(
    config: SmokeConfig,
    *,
    authorized_token: str,
    unauthorized_token: str,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ABC_LITERATURE_LIVE_ENABLE": "1",
            "ABC_LITERATURE_LIVE_BASE_URL": config.base_url,
            "ABC_LITERATURE_LIVE_TIMEOUT_SECONDS": str(config.literature_timeout_seconds),
            "ABC_LITERATURE_LIVE_UNKNOWN_MD5": config.unknown_md5,
            "ABC_LITERATURE_LIVE_KNOWN_MD5": config.known_md5,
            "ABC_LITERATURE_LIVE_RESTRICTED_MD5": config.restricted_md5,
            "ABC_LITERATURE_LIVE_PMID": config.pmid,
            "ABC_LITERATURE_LIVE_REFERENCE": config.reference,
            "ABC_LITERATURE_LIVE_CONVERTED_REFERENCEFILE_ID": (
                config.converted_referencefile_id
            ),
            "ABC_LITERATURE_LIVE_RESTRICTED_REFERENCEFILE_ID": (
                config.converted_referencefile_id
            ),
            "ABC_LITERATURE_LIVE_BEARER_TOKEN": authorized_token,
            "ABC_LITERATURE_LIVE_UNAUTHORIZED_BEARER_TOKEN": unauthorized_token,
        }
    )
    return env


def run_pytest(config: SmokeConfig, env: dict[str, str], command: Sequence[str]) -> CommandResult:
    try:
        completed = subprocess.run(
            list(command),
            cwd=config.repo_root / "backend",
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=config.pytest_timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(
            f"pytest live smoke timed out after {config.pytest_timeout_seconds:.0f}s"
        ) from exc
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def tail_text(value: str, limit: int = DEFAULT_EVIDENCE_TAIL_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _user_evidence(user: SmokeUser, *, deleted: bool) -> dict[str, Any]:
    return {
        "username": user.username,
        "email": user.email,
        "groups": list(user.groups),
        "deleted": deleted,
    }


def _pytest_command(config: SmokeConfig) -> tuple[str, ...]:
    return (
        config.python_executable,
        "-m",
        "pytest",
        "tests/live_integration/test_abc_literature_live_smoke.py",
        "-q",
        *config.pytest_args,
    )


def smoke_username(config: SmokeConfig, *, stamp: str, suffix: str, role: str) -> str:
    return f"{config.user_prefix}-{stamp.lower()}-{suffix}-{role}@example.invalid"


def run_smoke(
    config: SmokeConfig,
    *,
    aws_client_factory: AwsClientFactory = Boto3AwsSmokeClient,
    pytest_runner: PytestRunner = run_pytest,
    now: datetime | None = None,
) -> SmokeRunResult:
    now = now or _utc_now()
    stamp = _timestamp_for_file(now)
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = config.evidence_dir / f"abc_literature_live_smoke_{stamp}.json"
    suffix = secrets.token_hex(4)
    created_users: list[SmokeUser] = []
    deleted_users: set[str] = set()
    cleanup_failures: list[str] = []
    aws_client: AwsSmokeClient | None = None
    status = "fail"
    exit_code = 1

    evidence: dict[str, Any] = {
        "timestamp_utc": now.isoformat(),
        "overall_status": status,
        "base_url": config.base_url,
        "aws": {
            "profile": config.aws_profile,
            "region": config.region,
            "user_pool_id": config.user_pool_id,
            "client_id": config.client_id,
            "client_secret_provided": bool(config.client_secret),
            "api_timeout_seconds": config.aws_api_timeout_seconds,
        },
        "fixture": {
            "unknown_md5": config.unknown_md5,
            "known_md5": config.known_md5,
            "restricted_md5": config.restricted_md5,
            "pmid": config.pmid,
            "reference": config.reference,
            "source_referencefile_id": config.source_referencefile_id,
            "converted_referencefile_id": config.converted_referencefile_id,
        },
        "pytest": {
            "command": list(_pytest_command(config)),
            "timeout_seconds": config.pytest_timeout_seconds,
            "evidence_tail_limit": config.evidence_tail_limit,
        },
        "cleanup": {
            "keep_users": config.keep_users,
            "failures": cleanup_failures,
        },
    }

    try:
        aws_client = aws_client_factory(config)
        caller_identity = aws_client.caller_identity()
        if isinstance(caller_identity, dict):
            evidence["aws"]["caller_identity"] = {
                "account": caller_identity.get("Account"),
                "arn": caller_identity.get("Arn"),
                "user_id": caller_identity.get("UserId"),
            }

        if not config.client_secret:
            discovered_secret = aws_client.discover_client_secret()
            if discovered_secret:
                config = replace(config, client_secret=discovered_secret)
                evidence["aws"]["client_secret_provided"] = True
                evidence["aws"]["client_secret_source"] = "describe-user-pool-client"
            else:
                evidence["aws"]["client_secret_source"] = "not_available"
        else:
            evidence["aws"]["client_secret_source"] = "provided"

        authorized_user, authorized_password = create_smoke_user(
            config,
            username=smoke_username(
                config,
                stamp=stamp,
                suffix=suffix,
                role="authorized",
            ),
            groups=config.authorized_groups,
            aws_client=aws_client,
        )
        created_users.append(authorized_user)
        unauthorized_user, unauthorized_password = create_smoke_user(
            config,
            username=smoke_username(
                config,
                stamp=stamp,
                suffix=suffix,
                role="unauthorized",
            ),
            groups=(),
            aws_client=aws_client,
        )
        created_users.append(unauthorized_user)
        evidence["smoke_users"] = {
            "authorized": _user_evidence(authorized_user, deleted=False),
            "unauthorized": _user_evidence(unauthorized_user, deleted=False),
        }

        authorized_token = token_for_user(
            config,
            username=authorized_user.username,
            password=authorized_password,
            aws_client=aws_client,
        )
        unauthorized_token = token_for_user(
            config,
            username=unauthorized_user.username,
            password=unauthorized_password,
            aws_client=aws_client,
        )

        pytest_env = build_pytest_env(
            config,
            authorized_token=authorized_token,
            unauthorized_token=unauthorized_token,
        )
        command = _pytest_command(config)
        started = time.monotonic()
        pytest_result = pytest_runner(config, pytest_env, command)
        duration = time.monotonic() - started
        evidence["pytest"].update(
            {
                "returncode": pytest_result.returncode,
                "duration_seconds": round(duration, 3),
                "stdout_tail": tail_text(
                    redact_text(
                        pytest_result.stdout,
                        (authorized_token, unauthorized_token, config.client_secret or ""),
                    ),
                    config.evidence_tail_limit,
                ),
                "stderr_tail": tail_text(
                    redact_text(
                        pytest_result.stderr,
                        (authorized_token, unauthorized_token, config.client_secret or ""),
                    ),
                    config.evidence_tail_limit,
                ),
            }
        )
        if pytest_result.returncode != 0:
            status = "fail"
            exit_code = pytest_result.returncode
        else:
            status = "pass"
            exit_code = 0
    except Exception as exc:
        status = "fail"
        exit_code = 1
        evidence["error"] = {
            "type": type(exc).__name__,
            "message": redact_text(str(exc), (config.client_secret or "")),
        }
    finally:
        if config.keep_users:
            evidence["cleanup"]["skipped_reason"] = "--keep-users"
            if status == "pass":
                status = "debug_keep_users"
                exit_code = 1
        else:
            for user in reversed(created_users):
                try:
                    if aws_client is None:
                        raise SmokeFailure("AWS client unavailable for cleanup")
                    delete_smoke_user(user, aws_client)
                    deleted_users.add(user.username)
                except Exception as exc:
                    cleanup_failures.append(f"{user.username}: {type(exc).__name__}: {exc}")
            if cleanup_failures:
                status = "fail"
                exit_code = 1

        if created_users:
            evidence["smoke_users"] = {
                "authorized": _user_evidence(
                    created_users[0],
                    deleted=created_users[0].username in deleted_users,
                ),
                "unauthorized": _user_evidence(
                    created_users[1],
                    deleted=(
                        len(created_users) > 1
                        and created_users[1].username in deleted_users
                    ),
                )
                if len(created_users) > 1
                else None,
            }

        evidence["overall_status"] = status
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    return SmokeRunResult(exit_code=exit_code, evidence_path=evidence_path, evidence=evidence)


def main(argv: Sequence[str] | None = None) -> int:
    config = config_from_args(parse_args(argv))
    result = run_smoke(config)
    print("ABC Literature live smoke complete.")
    print(
        "Result: "
        f"{result.evidence['overall_status']} "
        f"(evidence={result.evidence_path})"
    )
    if result.evidence.get("error"):
        print(f"Error: {result.evidence['error']['message']}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
