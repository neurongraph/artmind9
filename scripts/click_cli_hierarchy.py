import importlib
import json
import sys

import click

# ---------------------------------------------------
# Load click object dynamically
# Example:
#   python inspect_click.py mypackage.cli:cli
# ---------------------------------------------------


def load_click_object(import_path):
    """
    Load a Click command/group from:
        module.submodule:object_name
    """

    if ":" not in import_path:
        raise ValueError("Import path must be in format: module.submodule:object")

    module_name, object_name = import_path.split(":", 1)

    module = importlib.import_module(module_name)

    return getattr(module, object_name)


# ---------------------------------------------------
# Extract options
# ---------------------------------------------------


def extract_options(cmd):
    options = []

    for param in cmd.params:
        option_info = {
            "name": param.name,
            "type": type(param).__name__,
            "required": getattr(param, "required", False),
            "default": getattr(param, "default", None),
            "help": getattr(param, "help", None),
        }

        if hasattr(param, "opts"):
            option_info["flags"] = param.opts

        if hasattr(param, "secondary_opts"):
            option_info["secondary_flags"] = param.secondary_opts

        options.append(option_info)

    return options


# ---------------------------------------------------
# Recursive extraction
# ---------------------------------------------------


def extract_command_tree(cmd, path=""):
    current_path = f"{path} {cmd.name}".strip()

    result = {
        "name": cmd.name,
        "path": current_path,
        "help": cmd.help,
        "short_help": cmd.short_help,
        "options": extract_options(cmd),
        "subcommands": [],
    }

    if isinstance(cmd, click.Group):
        for subcmd in cmd.commands.values():
            result["subcommands"].append(extract_command_tree(subcmd, current_path))

    return result


# ---------------------------------------------------
# Pretty print
# ---------------------------------------------------


def print_tree(node, indent=0):
    prefix = " " * indent

    print(f"{prefix}COMMAND: {node['path']}")

    if node["help"]:
        print(f"{prefix}  Help: {node['help']}")

    if node["options"]:
        print(f"{prefix}  Options:")

        for opt in node["options"]:
            flags = ", ".join(opt.get("flags", []))

            print(
                f"{prefix}    {flags} "
                f"(required={opt['required']}, "
                f"default={opt['default']})"
            )

            if opt["help"]:
                print(f"{prefix}      -> {opt['help']}")

    print()

    for child in node["subcommands"]:
        print_tree(child, indent + 4)


# ---------------------------------------------------
# Main
# ---------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage:\n  python inspect_click.py module.submodule:cli_object")
        sys.exit(1)

    import_path = sys.argv[1]

    root_cli = load_click_object(import_path)

    tree = extract_command_tree(root_cli)

    print("\n=== HUMAN READABLE TREE ===\n")
    print_tree(tree)
