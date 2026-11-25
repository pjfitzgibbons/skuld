# `plan suite`: runs this environment's test suite. What "the suite" is depends on the machine, so
# each environment writes it in a `.plan-suite` file: one shell command per line, blank lines and
# `#` lines ignored. The nearest file walking up from the current folder wins; outer ones are never
# consulted. Commands run in order from the folder holding that file, stopping at the first failure.
import subprocess
import sys
from pathlib import Path

CONFIG_NAME = ".plan-suite"


def findConfig(start):
    """The nearest .plan-suite in start or a parent, or None."""
    start = Path(start).resolve()
    for folder in (start, *start.parents):
        candidate = folder / CONFIG_NAME
        if candidate.is_file():
            return candidate
    return None


def isCommand(line):
    return bool(line.strip()) and not line.lstrip().startswith("#")


def commandsIn(file):
    return [line.strip() for line in Path(file).read_text().splitlines() if isCommand(line)]


def refuse(message):
    print(f"plan suite: {message}", file=sys.stderr)
    return 2


USAGE = "usage: plan suite [--dry-run]"


def runSuite(start, flags=()):
    """Exit code: 0 all passed, 2 refused (nothing run), else the first failing command's code."""
    if list(flags) not in ([], ["--dry-run"]):
        return refuse(USAGE)
    config = findConfig(start)
    if config is None:
        return refuse(f"no {CONFIG_NAME} in {Path(start).resolve()} or any folder above it")
    commands = commandsIn(config)
    if not commands:
        return refuse(f"{config} has no commands")
    if flags:
        return printPlan(config, commands)
    return runCommands(config, commands)


def printPlan(config, commands):
    print(f"# {config}")
    for command in commands:
        print(command)
    return 0


def runCommands(config, commands):
    for command in commands:
        print(f"== {command}", flush=True)  # flushed first, so a long command shows what is running
        code = subprocess.run(command, shell=True, cwd=config.parent).returncode
        if code != 0:
            return code
    print("suite: passed")
    return 0
