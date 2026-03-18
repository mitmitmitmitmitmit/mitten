"""MITTEN — personal Linux screen clip tool."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("mitten")
except PackageNotFoundError:
    __version__ = "unknown"
