# The WindowPlacer port: where an issue's VS Code window goes, how it's found again, and how the
# slots file labels it. Start and Open (lib/cli.py) talk to a placer, never to the window system
# directly. A page opens through term.showUrl(), which needs no placer: `code` finds the window.
# The one adapter is SpacesPlacer (lib/spaces.py), on native macOS Desktops.
#
# An adapter implements:
#
#   sweep()                         drop slot lines whose issue window is gone
#   openWindow(key, workspaceFile)  bring up the issue's window, or open one on a free slot -> Placement
#   focus(placement, workspaceFile) end on the issue's window; best-effort
#   slotLabels()                    {KEY: label} for `plan list`, a slot and where its window is
from dataclasses import dataclass, field


@dataclass
class Placement:
    created: bool
    slot: str
    where: str  # "Desktop 3": what `plan` prints
    notes: list = field(default_factory=list)


def placerFrom(cfg):
    from .spaces import SpacesPlacer  # spaces imports Placement from here

    return SpacesPlacer(cfg)
