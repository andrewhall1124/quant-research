"""The two ways a load can refuse.

Both name what to do next: `MissingDataset` names the pipeline command that
would create the file, `UntrustedSymbolYear` names the escape hatch that reads
the raw vendor record anyway.
"""


class MissingDataset(FileNotFoundError):
    """Raised with the pipeline command that would create the missing file."""


class UntrustedSymbolYear(ValueError):
    """Raised when a caller asks for a symbol-year the symbology check condemns."""
