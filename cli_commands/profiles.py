import json

from errors import CliError
import profiles as profiles_module


def cmd_profiles(args, _token=None, *, profiles=profiles_module):
    active, source = profiles.resolve_active_profile()
    names = profiles.list_profiles()
    if getattr(args, "json", False):
        print(json.dumps({
            "profilesDir": str(profiles.profiles_dir()),
            "active": active,
            "activeSource": source,
            "profiles": names,
        }, indent=2))
        return

    print(f"\nProfiles directory : {profiles.profiles_dir()}")
    print(f"Active profile     : {active + f' (from {source})' if active else '(none)'}")
    print()
    if not names:
        print("  No profiles found. Create <name>.env files (KEY=value lines) in the directory above.\n")
        return
    for name in names:
        marker = "*" if name == active else " "
        print(f"  {marker} {name}")
    print()


def cmd_use(args, _token=None, *, profiles=profiles_module):
    if getattr(args, "clear", False):
        profiles.set_active_profile(None)
        print("✓ Cleared the active profile; commands now use .env.local / .env only.")
        return

    name = getattr(args, "name", None)
    if not name:
        raise CliError("ERROR: Provide a profile name, or pass --clear to unset it.")
    if not profiles.is_valid_profile_name(name):
        raise CliError(f"ERROR: Invalid profile name '{name}'. Use letters, digits, '.', '_', or '-'.")
    env_file = profiles.profile_env_file(name)
    if not env_file.is_file():
        raise CliError(
            f"ERROR: No profile env file at {env_file}. Create it (KEY=value lines) and rerun."
        )
    profiles.set_active_profile(name)
    print(f"✓ Active profile set to '{name}'. Commands now layer {env_file} under real env vars.")


def register_profile_subcommands(sub):
    p = sub.add_parser("profiles", help="List project profiles and show the active one")
    p.add_argument("--json", action="store_true", help="Emit structured JSON output for scripting")

    p = sub.add_parser("use", help="Set or clear the active project profile")
    p.add_argument("name", nargs="?", help="Profile name (a <name>.env file in the profiles directory)")
    p.add_argument("--clear", action="store_true", help="Clear the active profile marker")


def profile_command_handlers() -> dict:
    return {"profiles": cmd_profiles, "use": cmd_use}
