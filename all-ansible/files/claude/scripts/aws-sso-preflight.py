#!/usr/bin/env python3
"""PreToolUse (Bash) hook: fail fast on AWS credential prompts that would hang.

Claude sessions hang when a Bash command resolves AWS credentials and something
along the way blocks on interactive input that nobody can answer:

  1. aws-vault's SSO token is stale -> aws-vault runs the OIDC device flow and
     polls forever.
  2. The aws-vault keychain is locked -> aws-vault must read its cached
     role-credential session from it, which pops a macOS unlock dialog. Unlike
     SSO, this is a local-only GUI with no remote/device-code equivalent -- but
     the underlying native SSO profile can be used directly to skip it.

The plain `aws` CLI does not hang: with no usable token it exits immediately with
"Error loading SSO Token". That is still worth catching, because the message
gives no route to a fix, but it is a fast failure rather than a hang -- and it
only happens when the CLI has nothing left to fall back on. Two fallbacks make an
expired token a non-event, and neither is visible in the token's expiry:

  - Role credentials in ~/.aws/cli/cache outlive the OIDC token, for as long as
    the permission set's session duration allows. While one is valid the CLI
    serves it without consulting the token at all.
  - A token carrying a refreshToken (with a live client registration) is renewed
    silently, with no prompt and no browser.

Blocking without checking those turns a working command into a needless request
for the user to re-authenticate.

It also catches a related dead-end: an `aws` command with no profile selected
when no [default] profile exists. That doesn't hang -- it fails with "Unable to
locate credentials" -- so the hook blocks and steers Claude to re-run with a
profile: the fitting one if the context makes it obvious, otherwise asking the
user rather than guessing.

This hook inspects the command, and for each hazard checks the relevant state
*without side effects* (no network, no browser, no keychain prompt). If a
command would hang, it blocks (exit 2) and tells Claude how to get the user to
resolve it.

Generic: profiles, sso-sessions and the credential_process -> profile chain are
read from ~/.aws/config, so nothing here is specific to one account.

Run the behaviour tests with:  python3 aws-sso-preflight.py --selftest
"""

from __future__ import annotations

import base64
import configparser
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

AWS_DIR = Path(os.environ.get("AWS_CONFIG_DIR", Path.home() / ".aws"))
CONFIG_PATH = Path(os.environ.get("AWS_CONFIG_FILE", AWS_DIR / "config"))
SSO_CACHE = AWS_DIR / "sso" / "cache"
CLI_CACHE = AWS_DIR / "cli" / "cache"
KEYCHAIN_DIR = Path.home() / "Library" / "Keychains"

# Only gate commands that actually resolve AWS credentials. Matching is by
# command head (see command_segments), never by substring: an AWS name inside a
# quoted string, in a longer path like scripts/aws-sso-preflight.py, or in an
# unrelated token like aws-access is not an invocation and must not gate.
AWS_EXECUTABLES = {"aws", "aws-vault", "terraform"}

# Treat a token/session as stale this many seconds early, to avoid a race where
# it expires mid-command.
SKEW_SECONDS = 60


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # Not our contract; never block on malformed input.

    command = data.get("tool_input", {}).get("command", "")

    no_profile = check_no_profile(command)
    if no_profile:
        block_no_profile(no_profile)
        return 2

    issues = check(command)
    if not issues:
        return 0

    block(issues)
    return 2


def check(command: str) -> list[str]:
    """The hang hazards a command would hit, as ready-to-print blurbs.

    Empty list means the command is safe to run."""
    if not gates_credentials(command):
        return []

    profile = target_profile(command)
    if profile is None:
        return []  # No profile in play (e.g. env creds); don't interfere.

    session = resolve_sso_session(profile)
    keychain = aws_vault_keychain(command, profile)

    if keychain is not None:
        # aws-vault ecosystem: its own keychain holds the authoritative OIDC
        # token and role-cred session. The ~/.aws/sso/cache files are a stale
        # artifact aws-vault never updates, so they must NOT be consulted here.
        if keychain_locked(keychain):
            return [keychain_issue(keychain, profile, session)]
        if not aws_vault_session_fresh(profile, keychain):
            return [vault_sso_issue(profile)]
        return []

    # Native SSO ecosystem (profile used directly, no aws-vault). An expired token
    # only strands the command when the CLI has no fallback: cached role creds it
    # can serve directly, or a refresh token it can redeem unattended.
    if session is not None and not session_is_fresh(session):
        if cached_role_credentials_fresh(profile):
            return []
        if token_is_refreshable(session):
            return []
        return [sso_issue(session)]
    return []


def check_no_profile(command: str) -> str | None:
    """Blurb for an `aws` command that would fail for lack of a profile, or None.

    Fires only when nothing selects a profile (no --profile, no AWS_PROFILE, no
    static env creds) AND ~/.aws/config has no [default] to fall back on -- so
    the command is doomed to "Unable to locate credentials". A [default] profile,
    if present, is left to check() (it may still need an SSO refresh)."""
    if not gates_credentials(command):
        return None
    if not is_bare_aws_command(command):
        return None  # aws-vault/terraform, or an `aws` subcommand needing no creds.
    if explicit_profile(command) is not None or os.environ.get("AWS_PROFILE"):
        return None  # a profile was chosen explicitly or via the environment.
    if env_credentials() or has_default_profile():
        return None  # static env creds, or a [default] profile check() will vet.
    return no_profile_issue()


# --- command -> profile -------------------------------------------------------

def target_profile(command: str) -> str | None:
    """Best-effort extraction of the AWS profile a command will use."""
    explicit = explicit_profile(command)
    if explicit is not None:
        return explicit

    env_profile = os.environ.get("AWS_PROFILE")
    if env_profile:
        return env_profile

    return "default"


def explicit_profile(command: str) -> str | None:
    """The profile a command names outright (flag, env assignment, or aws-vault
    argument), or None if it leaves the choice to AWS_PROFILE / the default."""
    # Shell metacharacters end the name: inside $(aws ... --profile prod) the
    # closing paren would otherwise be read as part of the profile.
    m = re.search(r"--profile[=\s]+([^\s'\";|&()]+)", command)
    if m:
        return m.group(1)

    m = re.search(r"\bAWS_PROFILE=([^\s'\";|&()]+)", command)
    if m:
        return m.group(1)

    # aws-vault export/exec <flags...> <profile>
    m = re.search(r"\baws-vault\s+(?:export|exec)\b(.*)", command)
    if m:
        args = [a for a in m.group(1).split() if not a.startswith("-")]
        if args:
            return args[-1]

    return None


# --- profile -> sso session ---------------------------------------------------

def resolve_sso_session(profile: str, _seen: set[str] | None = None) -> str | None:
    """Follow a profile to the sso-session name (or legacy start URL) whose
    cached token backs it, chasing credential_process/source_profile links."""
    _seen = _seen or set()
    if profile in _seen:
        return None
    _seen.add(profile)

    cfg = load_config()
    section = section_for(profile)
    if not cfg.has_section(section):
        return None

    if cfg.has_option(section, "sso_session"):
        return cfg.get(section, "sso_session")

    # Legacy inline SSO config (no [sso-session]); token keyed by start URL.
    if cfg.has_option(section, "sso_start_url"):
        return f"legacy:{cfg.get(section, 'sso_start_url')}"

    if cfg.has_option(section, "credential_process"):
        chained = last_profile_arg(cfg.get(section, "credential_process"))
        if chained:
            return resolve_sso_session(chained, _seen)

    if cfg.has_option(section, "source_profile"):
        return resolve_sso_session(cfg.get(section, "source_profile"), _seen)

    return None


def session_is_fresh(session: str) -> bool:
    expires = read_expiry(token_path(session))
    return expires is not None and expires > now() + SKEW_SECONDS


def token_path(session: str) -> Path:
    key = session[len("legacy:"):] if session.startswith("legacy:") else session
    return SSO_CACHE / (sha1(key) + ".json")


def token_is_refreshable(session: str) -> bool:
    """Whether an expired token can be renewed with no user interaction.

    The CLI redeems a refreshToken against the client registration, so both must
    be present and the registration still valid. A refresh that fails server-side
    (the Identity Center session was revoked) errors immediately rather than
    prompting, so treating this as usable costs a fast failure at worst."""
    token = read_json(token_path(session))
    if not token or not token.get("refreshToken"):
        return False
    registration = parse_timestamp(token.get("registrationExpiresAt"))
    if registration is None:
        return False
    return registration > now() + SKEW_SECONDS


def cached_role_credentials_fresh(profile: str) -> bool:
    """Whether the CLI already holds unexpired role credentials for the account
    the profile targets, which it serves without consulting the SSO token.

    Entries are matched on account rather than by recomputing the CLI's cache key,
    which is an internal detail. Where one account is reached through several
    roles this can match a sibling role's credentials; the cost is a command that
    proceeds and then fails fast, never a hang."""
    account = profile_account_id(profile)
    if account is None:
        return False

    try:
        entries = list(CLI_CACHE.glob("*.json"))
    except OSError:
        return False

    for path in entries:
        cached = read_json(path)
        if not cached:
            continue
        credentials = cached.get("Credentials") or {}
        if credentials.get("AccountId") != account:
            continue
        expiry = parse_timestamp(credentials.get("Expiration"))
        if expiry is not None and expiry > now() + SKEW_SECONDS:
            return True
    return False


def profile_account_id(profile: str, _seen: set[str] | None = None) -> str | None:
    """The account a profile resolves to, following the same chain as
    resolve_sso_session so a credential_process wrapper still reports its base."""
    _seen = _seen or set()
    if profile in _seen:
        return None
    _seen.add(profile)

    cfg = load_config()
    section = section_for(profile)
    if not cfg.has_section(section):
        return None

    if cfg.has_option(section, "sso_account_id"):
        return cfg.get(section, "sso_account_id")

    if cfg.has_option(section, "credential_process"):
        chained = last_profile_arg(cfg.get(section, "credential_process"))
        if chained:
            return profile_account_id(chained, _seen)

    if cfg.has_option(section, "source_profile"):
        return profile_account_id(cfg.get(section, "source_profile"), _seen)

    return None


# --- aws-vault keychain -------------------------------------------------------

def aws_vault_keychain(command: str, profile: str) -> Path | None:
    """Path of the aws-vault keychain a command would touch, or None if the
    keychain backend isn't in play."""
    backend = os.environ.get("AWS_VAULT_BACKEND")
    if backend and backend != "keychain":
        return None  # pass/file/1Password backends don't use the macOS keychain.

    if not invokes_aws_vault(command) and not chain_uses_aws_vault(profile):
        return None

    name = os.environ.get("AWS_VAULT_KEYCHAIN_NAME", "aws-vault")
    path = KEYCHAIN_DIR / f"{name}.keychain-db"
    return path if path.exists() else None


def chain_uses_aws_vault(profile: str, _seen: set[str] | None = None) -> bool:
    _seen = _seen or set()
    if profile in _seen:
        return False
    _seen.add(profile)

    cfg = load_config()
    section = section_for(profile)
    if not cfg.has_section(section):
        return False

    if cfg.has_option(section, "credential_process"):
        proc = cfg.get(section, "credential_process")
        if "aws-vault" in proc:
            return True
        chained = last_profile_arg(proc)
        if chained:
            return chain_uses_aws_vault(chained, _seen)

    if cfg.has_option(section, "source_profile"):
        return chain_uses_aws_vault(cfg.get(section, "source_profile"), _seen)

    return False


def native_sso_profile(profile: str, _seen: set[str] | None = None) -> str | None:
    """The nearest profile in the chain that is usable natively (SSO config, no
    credential_process), i.e. resolvable without aws-vault or its keychain."""
    _seen = _seen or set()
    if profile in _seen:
        return None
    _seen.add(profile)

    cfg = load_config()
    section = section_for(profile)
    if not cfg.has_section(section):
        return None

    has_process = cfg.has_option(section, "credential_process")
    is_sso = cfg.has_option(section, "sso_session") or cfg.has_option(section, "sso_start_url")
    if is_sso and not has_process:
        return profile

    if has_process:
        chained = last_profile_arg(cfg.get(section, "credential_process"))
        if chained:
            return native_sso_profile(chained, _seen)

    if cfg.has_option(section, "source_profile"):
        return native_sso_profile(cfg.get(section, "source_profile"), _seen)

    return None


def keychain_locked(path: Path) -> bool:
    """Read the keychain's lock state via SecKeychainGetStatus, which never
    prompts. Fails open (reports unlocked) if the API can't be reached, so a
    detection glitch can't false-block normal work."""
    try:
        sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    except OSError:
        return False

    keychain_ref = ctypes.c_void_p()
    sec.SecKeychainOpen.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p)]
    sec.SecKeychainOpen.restype = ctypes.c_int32
    sec.SecKeychainGetStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    sec.SecKeychainGetStatus.restype = ctypes.c_int32

    if sec.SecKeychainOpen(str(path).encode(), ctypes.byref(keychain_ref)) != 0:
        return False

    status = ctypes.c_uint32(0)
    if sec.SecKeychainGetStatus(keychain_ref, ctypes.byref(status)) != 0:
        return False

    # kSecUnlockStateStatus = 1; its absence means locked.
    return not (status.value & 1)


def aws_vault_session_fresh(profile: str, keychain: Path) -> bool:
    """Whether aws-vault holds a still-valid cached role-cred session for the
    profile -- i.e. it can return creds without any SSO login. Read from the
    (unlocked) keychain; a stale/absent session means aws-vault would re-login."""
    managed = native_sso_profile(profile) or profile
    expiry = aws_vault_session_expiry(managed, keychain)
    return expiry is not None and expiry > now() + SKEW_SECONDS


def aws_vault_session_expiry(managed_profile: str, keychain: Path) -> int | None:
    """Latest cached role-cred session expiry (unix epoch) aws-vault holds for
    the profile. aws-vault encodes it in the item's account attribute
    (sso.GetRoleCredentials,<b64 profile>,<b64 startUrl>,<epoch>); reading
    attributes (not the secret) doesn't prompt while the keychain is unlocked."""
    token = base64.b64encode(managed_profile.encode()).decode().rstrip("=")
    try:
        out = subprocess.run(
            ["/usr/bin/security", "dump-keychain", str(keychain)],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    best: int | None = None
    for match in re.finditer(r'"acct"<blob>="(sso\.GetRoleCredentials,[^"]*)"', out):
        parts = match.group(1).split(",")
        if len(parts) >= 4 and parts[1].rstrip("=") == token:
            try:
                epoch = int(parts[-1])
            except ValueError:
                continue
            best = epoch if best is None else max(best, epoch)
    return best


# --- output -------------------------------------------------------------------

def sso_issue(session: str) -> str:
    if session.startswith("legacy:"):
        login = "aws sso login"  # legacy profiles: session is implicit
        label = "the AWS SSO session"
    else:
        login = f"aws sso login --sso-session {session}"
        label = f'the "{session}" AWS SSO session'
    device_login = login.replace(
        "aws sso login", "aws sso login --use-device-code --no-browser", 1
    )
    # Which side a prompt was typed from isn't knowable to the hook, so present
    # both routes to the user and let them pick from the notification.
    return f"""* {label} is expired/missing, with no cached role credentials and no usable
  refresh token, so the command exits immediately with "Error loading SSO Token".
  It will not hang, but it cannot succeed until the session is renewed.
  Show the user BOTH routes below and let THEM choose -- do not pick one yourself:
    A) At the Mac -- opens a browser locally:
         {login}   (or type:  ! {login} )
    B) Remote / away from the Mac -- prints a URL + code to complete on any device:
         {device_login}
       Relay the URL + code from its output; the user approves on their phone."""


def vault_sso_issue(profile: str) -> str:
    managed = native_sso_profile(profile) or profile
    # aws-vault keeps its own SSO token; `aws sso login` would refresh the wrong
    # (native CLI) store, so re-login must go through aws-vault itself.
    return f"""* aws-vault has no valid cached session for "{managed}"; resolving creds would
  run its SSO login (browser) and hang. Show the user BOTH routes and let THEM
  choose -- do not pick one yourself:
    A) At the Mac -- opens a browser to complete the login:
         aws-vault export {managed}
    B) Remote / away from the Mac -- prints the SSO link to the terminal instead
       of opening a browser; relay it (send a PushNotification) so the user can
       approve on their phone, then it continues:
         aws-vault export --stdout {managed}"""


def keychain_issue(path: Path, profile: str, session: str | None) -> str:
    name = path.stem
    lines = [
        f'* The "{name}" keychain is locked; aws-vault must read it and would pop a',
        "  macOS unlock dialog -- a LOCAL-only GUI with no remote/device-code route.",
    ]
    native = native_sso_profile(profile)
    # Only offer the native bypass if it would actually work right now: it reads
    # ~/.aws/sso/cache, so it's useless if that token is also stale.
    bypass_ok = bool(native and native != profile and session and session_is_fresh(session))

    options = []
    if bypass_ok:
        options.append([
            "Remote -- the native SSO profile is valid right now, so skip",
            "aws-vault entirely (file-cached, no keychain):",
            f"  --profile {native}",
        ])
    options.append([
        "At the Mac -- unlock the keychain with any aws-vault command (enter",
        "the keychain password at the prompt), then re-run the original:",
        "  aws-vault list",
    ])

    if len(options) > 1:
        lines.append("  Show the user BOTH choices below and let THEM choose -- do not pick one yourself:")
    else:
        lines.append("  Resolve it with:")
    for label, opt in zip("AB", options):
        lines.append(f"    {label}) {opt[0]}")
        lines += [f"       {line}" for line in opt[1:]]
    return "\n".join(lines)


def no_profile_issue() -> str:
    profiles = usable_profiles()
    listing = ", ".join(profiles) if profiles else "(none defined in ~/.aws/config)"
    return f"""* No AWS profile is selected: the command names none, AWS_PROFILE is unset,
  and ~/.aws/config has no [default] -- so it will fail with "Unable to locate
  credentials". Re-run with the profile that fits the task: if the context
  already makes the right one clear (the user named it, or the work is plainly
  scoped to one account), just use it --  <command> --profile <name>  (or prefix
  AWS_PROFILE=<name>). Only when it's genuinely ambiguous, ask the user which to
  use (send a PushNotification) rather than guessing.
  Available profiles: {listing}"""


def block_no_profile(issue: str) -> None:
    print(
        "BLOCKED: this AWS command has no profile selected and would fail to "
        "locate credentials. Re-run with the profile that fits the task -- use "
        "the obvious one if the context makes it clear, otherwise ask the user "
        "which to use.\n\n"
        f"{issue}",
        file=sys.stderr,
    )


def block(issues: list[str]) -> None:
    body = "\n".join(issues)
    print(
        "BLOCKED: this command cannot resolve AWS credentials -- it would hang on "
        "an interactive prompt, or fail with no route to a fix. Do NOT retry, and "
        "do NOT choose a fix yourself. Send the user a PushNotification and "
        "present the choices in each item below for them to pick from; re-run the "
        f"original command once they confirm it's resolved.\n\n{body}",
        file=sys.stderr,
    )


# --- helpers ------------------------------------------------------------------

_config_cache: configparser.ConfigParser | None = None


def load_config() -> configparser.ConfigParser:
    global _config_cache
    if _config_cache is None:
        cfg = configparser.ConfigParser()
        try:
            cfg.read(CONFIG_PATH)
        except configparser.Error:
            pass
        _config_cache = cfg
    return _config_cache


def section_for(profile: str) -> str:
    return f"profile {profile}" if profile != "default" else "default"


# `aws` subcommands that never resolve credentials, so a missing profile is fine.
SAFE_AWS_SUBCOMMANDS = {"configure", "help", "sso", "sso-oidc", "version", "--version"}


def is_bare_aws_command(command: str) -> bool:
    """Whether the command invokes the `aws` CLI (not aws-vault) on a subcommand
    that actually resolves credentials -- the only case a missing profile dooms."""
    for tokens in aws_invocations(command):
        if executable(tokens) != "aws":
            continue
        args = [t for t in tokens[1:] if not t.startswith("-")]
        sub = args[0] if args else None
        if sub is not None and sub not in SAFE_AWS_SUBCOMMANDS:
            return True
    return False


# Separators after which the next word is a new command's executable. Backticks
# are deliberately absent: prose and markdown spans write `aws`, which would read
# as a head once split on them, and $(...) covers the substitution that matters.
SEGMENT_BOUNDARY = re.compile(r"\|\||&&|[;&|(){}\n]|\$\(")

ENV_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")

# Wrappers that run whatever follows them, so the real executable is further on.
COMMAND_WRAPPERS = {"sudo", "doas", "env", "command", "time", "nohup", "exec", "builtin"}


def gates_credentials(command: str) -> bool:
    """Whether a command would resolve AWS credentials at all."""
    return bool(command) and bool(aws_invocations(command))


def aws_invocations(command: str) -> list[list[str]]:
    """Tokens of each command in the line that runs an AWS tool, plus any that
    sets AWS_PROFILE for whatever it runs (e.g. a wrapper script)."""
    found = []
    for assignments, tokens in command_segments(command):
        if executable(tokens) in AWS_EXECUTABLES or "AWS_PROFILE" in assignments:
            found.append(tokens)
    return found


def command_segments(command: str) -> list[tuple[list[str], list[str]]]:
    """Each command in a compound line, as (env assignment names, tokens).

    Splitting on shell separators means only a genuine executable position is
    considered, so an AWS name in prose or a quoted string never counts. The
    known gap: a separator inside a quoted string can still expose the word after
    it as a head, so `echo "x; aws s3 ls"` reads as an invocation."""
    segments = []
    for raw in SEGMENT_BOUNDARY.split(command):
        tokens = raw.split()
        assignments = []
        while tokens and (ENV_ASSIGNMENT.match(tokens[0]) or tokens[0] in COMMAND_WRAPPERS):
            match = ENV_ASSIGNMENT.match(tokens.pop(0))
            if match:
                assignments.append(match.group(1))
        if tokens or assignments:
            segments.append((assignments, tokens))
    return segments


def invokes_aws_vault(command: str) -> bool:
    return any(executable(tokens) == "aws-vault" for tokens in aws_invocations(command))


def executable(tokens: list[str]) -> str:
    """The program a segment runs, by basename, so /usr/local/bin/aws is aws
    while scripts/aws-sso-preflight.py is not."""
    return Path(tokens[0]).name if tokens else ""


def env_credentials() -> bool:
    """Whether static credentials are supplied via the environment, in which case
    no profile is needed."""
    return bool(os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"))


def has_default_profile() -> bool:
    return load_config().has_section("default")


def usable_profiles() -> list[str]:
    """Profile names worth offering the user: every configured profile minus the
    base profiles that exist only to back another via credential_process /
    source_profile (e.g. the *-sso bases behind aws-vault profiles)."""
    cfg = load_config()
    bases = referenced_base_profiles(cfg)
    names = []
    for section in cfg.sections():
        if section == "default":
            name = "default"
        elif section.startswith("profile "):
            name = section[len("profile "):]
        else:
            continue  # e.g. [sso-session ...] -- not a profile.
        if name not in bases:
            names.append(name)
    return names


def referenced_base_profiles(cfg: configparser.ConfigParser) -> set[str]:
    bases: set[str] = set()
    for section in cfg.sections():
        if cfg.has_option(section, "credential_process"):
            chained = last_profile_arg(cfg.get(section, "credential_process"))
            if chained:
                bases.add(chained)
        if cfg.has_option(section, "source_profile"):
            bases.add(cfg.get(section, "source_profile"))
    return bases


def read_json(path: Path) -> dict | None:
    try:
        loaded = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def parse_timestamp(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def read_expiry(token_file: Path) -> float | None:
    cached = read_json(token_file)
    if not cached:
        return None
    return parse_timestamp(cached.get("expiresAt") or cached.get("ExpiresAt"))


def last_profile_arg(command: str) -> str | None:
    args = [a for a in command.split() if not a.startswith("-")]
    while args and args[0] in ("aws-vault", "export", "exec", "aws"):
        args.pop(0)
    return args[-1] if args else None


def sha1(value: str) -> str:
    return hashlib.sha1(value.encode()).hexdigest()


def tilde(path: Path) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text.startswith(home) else text


def now() -> float:
    return datetime.now(timezone.utc).timestamp()


# --- self-test ----------------------------------------------------------------
# Behaviour tests over the real seam (check()): a realistic ~/.aws/config and SSO
# token cache on disk, with keychain lock state faked (the only thing that needs
# a live macOS keychain). Run: python3 aws-sso-preflight.py --selftest

def _selftest() -> int:
    import tempfile
    import unittest
    from unittest import mock

    module = sys.modules[__name__]

    CONFIG = """
[sso-session geckoboard]
sso_region = us-east-1
sso_start_url = https://example.awsapps.com/start

[profile prod-sso]
sso_session = geckoboard
sso_account_id = 111111111111
sso_role_name = AdminAccess

[profile prod]
region = us-east-1
credential_process = aws-vault export --format=json prod-sso

[profile plainuser]
region = us-east-1
"""

    class HookTests(unittest.TestCase):
        def setUp(self):
            self.tmp = Path(tempfile.mkdtemp())
            self._orig = {k: getattr(module, k)
                          for k in ("CONFIG_PATH", "SSO_CACHE", "CLI_CACHE",
                                    "KEYCHAIN_DIR", "_config_cache")}
            config = self.tmp / "config"
            config.write_text(CONFIG)
            self.sso_cache = self.tmp / "sso" / "cache"
            self.sso_cache.mkdir(parents=True)
            self.cli_cache = self.tmp / "cli" / "cache"
            self.cli_cache.mkdir(parents=True)
            self.keychain_dir = self.tmp / "Keychains"
            self.keychain_dir.mkdir()
            (self.keychain_dir / "aws-vault.keychain-db").write_text("")  # existence only

            module.CONFIG_PATH = config
            module.SSO_CACHE = self.sso_cache
            module.CLI_CACHE = self.cli_cache
            module.KEYCHAIN_DIR = self.keychain_dir
            module._config_cache = None

        def tearDown(self):
            for k, v in self._orig.items():
                setattr(module, k, v)

        @staticmethod
        def stamp(offset: float) -> str:
            when = datetime.fromtimestamp(now() + offset, timezone.utc)
            return when.isoformat().replace("+00:00", "Z")

        def set_token(self, *, expired: bool, refreshable: bool = False,
                      registration_expired: bool = False):
            body = {"expiresAt": self.stamp(-3600 if expired else 3600)}
            if refreshable:
                body["refreshToken"] = "rt-abc"
                body["registrationExpiresAt"] = self.stamp(
                    -3600 if registration_expired else 86400)
            token = self.sso_cache / (sha1("geckoboard") + ".json")
            token.write_text(json.dumps(body))

        def set_cached_role_creds(self, *, account: str, expired: bool = False):
            """A ~/.aws/cli/cache entry as the CLI writes it for an SSO profile."""
            body = {
                "ProviderType": "sso",
                "Credentials": {
                    "AccessKeyId": "AKIA", "SecretAccessKey": "s", "SessionToken": "t",
                    "Expiration": self.stamp(-3600 if expired else 3600),
                    "AccountId": account,
                },
            }
            (self.cli_cache / f"{account}.json").write_text(json.dumps(body))

        def check(self, command, *, locked=False, vault_fresh=True, env=None):
            # aws-vault session freshness comes from keychain metadata, faked here;
            # native (~/.aws/sso/cache) freshness comes from the real token file.
            epoch = (now() + 3600) if vault_fresh else None
            with mock.patch.object(module, "keychain_locked", return_value=locked), \
                 mock.patch.object(module, "aws_vault_session_expiry", return_value=epoch), \
                 mock.patch.dict(os.environ, env or {}, clear=False):
                return module.check(command)

        # --- aws-vault-backed profile: truth is aws-vault's keychain ---

        def test_vault_profile_with_fresh_vault_session_is_allowed(self):
            # ~/.aws/sso/cache is EXPIRED, but aws-vault's own session is fresh:
            # this is the false-positive we must not raise.
            self.set_token(expired=True)
            self.assertEqual(self.check("aws s3 ls --profile prod", vault_fresh=True), [])

        def test_vault_profile_with_stale_vault_session_flags_relogin(self):
            self.set_token(expired=False)  # native cache irrelevant for a vault profile
            issues = self.check("aws s3 ls --profile prod", vault_fresh=False)
            self.assertEqual(len(issues), 1)
            self.assertIn("aws-vault has no valid cached session", issues[0])
            self.assertIn("aws-vault export prod-sso", issues[0])
            self.assertIn("--stdout", issues[0])  # remote route
            self.assertNotIn("aws sso login", issues[0])  # wrong store

        # --- locked keychain ---

        def test_locked_keychain_offers_bypass_when_native_token_fresh(self):
            self.set_token(expired=False)
            issues = self.check("aws s3 ls --profile prod", locked=True)
            self.assertEqual(len(issues), 1)
            self.assertIn("keychain is locked", issues[0])
            self.assertIn("--profile prod-sso", issues[0])  # bypass usable now
            self.assertIn("aws-vault list", issues[0])       # unlock at the Mac
            self.assertNotIn("security ", issues[0])

        def test_locked_keychain_omits_bypass_when_native_token_stale(self):
            self.set_token(expired=True)
            issues = self.check("aws s3 ls --profile prod", locked=True)
            self.assertEqual(len(issues), 1)
            self.assertIn("keychain is locked", issues[0])
            self.assertNotIn("--profile prod-sso", issues[0])  # bypass would also hang
            self.assertIn("aws-vault list", issues[0])

        # --- native SSO profile: truth is ~/.aws/sso/cache ---

        def test_native_profile_fresh_token_is_allowed(self):
            self.set_token(expired=False)
            self.assertEqual(self.check("aws s3 ls --profile prod-sso", locked=True), [])

        def test_native_profile_expired_token_flags_sso(self):
            self.set_token(expired=True)
            issues = self.check("aws s3 ls --profile prod-sso")
            self.assertEqual(len(issues), 1)
            self.assertIn("SSO session is expired", issues[0])
            self.assertIn("--use-device-code", issues[0])
            self.assertIn("will not hang", issues[0])  # fast failure, not a hang

        # --- fallbacks that make an expired native token a non-event ---

        def test_expired_token_allowed_when_role_creds_cached(self):
            # The case that cost real round-trips: the OIDC token lapses after an
            # hour while the role credentials behind it run for the permission
            # set's full session duration.
            self.set_token(expired=True)
            self.set_cached_role_creds(account="111111111111")
            self.assertEqual(self.check("aws s3 ls --profile prod-sso"), [])

        def test_expired_token_flagged_when_cached_creds_also_expired(self):
            self.set_token(expired=True)
            self.set_cached_role_creds(account="111111111111", expired=True)
            self.assertEqual(len(self.check("aws s3 ls --profile prod-sso")), 1)

        def test_cached_creds_for_another_account_do_not_count(self):
            self.set_token(expired=True)
            self.set_cached_role_creds(account="999999999999")
            self.assertEqual(len(self.check("aws s3 ls --profile prod-sso")), 1)

        def test_expired_token_allowed_when_refreshable(self):
            self.set_token(expired=True, refreshable=True)
            self.assertEqual(self.check("aws s3 ls --profile prod-sso"), [])

        def test_expired_token_flagged_when_registration_expired(self):
            # A refresh token is useless once its client registration lapses.
            self.set_token(expired=True, refreshable=True, registration_expired=True)
            self.assertEqual(len(self.check("aws s3 ls --profile prod-sso")), 1)

        def test_vault_profile_ignores_cli_credential_cache(self):
            # A vault profile resolves through credential_process, which the CLI
            # cache never backs, so a fresh entry must not mask a stale session.
            self.set_token(expired=True)
            self.set_cached_role_creds(account="111111111111")
            issues = self.check("aws s3 ls --profile prod", vault_fresh=False)
            self.assertEqual(len(issues), 1)
            self.assertIn("aws-vault has no valid cached session", issues[0])

        # --- gating ---

        def test_non_aws_command_is_ignored(self):
            self.set_token(expired=True)
            self.assertEqual(self.check("echo hello world", locked=True, vault_fresh=False), [])

        def test_profile_without_sso_is_ignored(self):
            self.set_token(expired=True)
            self.assertEqual(self.check("aws s3 ls --profile plainuser", vault_fresh=False), [])

        # --- an AWS name that isn't an invocation ---

        def assert_ignored(self, command):
            self.set_token(expired=True)
            self.assertEqual(self.check(command, locked=True, vault_fresh=False), [], command)
            self.assertIsNone(self.no_profile(command), command)

        def test_longer_token_starting_with_aws_is_ignored(self):
            self.assert_ignored("for d in aws-access circleci; do echo $d; done")

        def test_path_to_a_file_named_aws_something_is_ignored(self):
            self.assert_ignored("git add scripts/aws-sso-preflight.py")

        def test_aws_named_in_quoted_prose_is_ignored(self):
            self.assert_ignored('echo "aws-vault pops a dialog, so run something else"')

        def test_aws_profile_named_in_quoted_prose_is_ignored(self):
            self.assert_ignored("""echo 'set AWS_PROFILE=<name> to pick an account'""")

        # --- real invocations, in positions the head parse must still catch ---

        def assert_flagged(self, command):
            self.set_token(expired=False)
            self.assertEqual(len(self.check(command, vault_fresh=False)), 1, command)

        def test_aws_after_a_separator_is_detected(self):
            self.assert_flagged("cd /tmp && aws s3 ls --profile prod")

        def test_env_assignment_before_aws_is_detected(self):
            self.assert_flagged("AWS_PROFILE=prod aws s3 ls")

        def test_command_substitution_is_detected(self):
            self.assert_flagged("echo $(aws sts get-caller-identity --profile prod)")

        def test_absolute_path_to_aws_is_detected(self):
            self.assert_flagged("/usr/local/bin/aws s3 ls --profile prod")

        def test_wrapper_before_aws_is_detected(self):
            self.assert_flagged("time aws s3 ls --profile prod")

        # --- no profile selected (no [default]) ---

        def no_profile(self, command, env=None):
            # A clean environment: no inherited AWS_PROFILE or static creds.
            with mock.patch.dict(os.environ, env or {}, clear=True):
                return module.check_no_profile(command)

        def test_no_profile_flags_and_lists_usable_profiles(self):
            issue = self.no_profile("aws s3 ls")
            self.assertIsNotNone(issue)
            self.assertIn("No AWS profile is selected", issue)
            self.assertIn("prod", issue)
            self.assertIn("plainuser", issue)
            self.assertNotIn("prod-sso", issue)  # a base profile, not offered

        def test_no_profile_allows_explicit_profile(self):
            self.assertIsNone(self.no_profile("aws s3 ls --profile prod"))

        def test_no_profile_allows_env_profile(self):
            self.assertIsNone(self.no_profile("aws s3 ls", env={"AWS_PROFILE": "prod"}))

        def test_no_profile_allows_env_credentials(self):
            self.assertIsNone(self.no_profile(
                "aws s3 ls", env={"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s3cr3t"}))

        def test_no_profile_allows_when_default_profile_exists(self):
            (self.tmp / "config").write_text(CONFIG + "\n[default]\nregion = us-east-1\n")
            module._config_cache = None
            self.assertIsNone(self.no_profile("aws s3 ls"))

        def test_no_profile_ignores_credentialless_subcommands(self):
            for command in ("aws configure list-profiles", "aws --version", "aws sso login"):
                self.assertIsNone(self.no_profile(command), command)

        def test_no_profile_ignores_aws_vault_and_non_aws(self):
            self.assertIsNone(self.no_profile("aws-vault list"))
            self.assertIsNone(self.no_profile("aws-vault exec prod -- aws s3 ls"))
            self.assertIsNone(self.no_profile("echo hello"))

    suite = unittest.TestLoader().loadTestsFromTestCase(HookTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main())
