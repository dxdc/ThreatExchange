#!/usr/bin/env python
# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

"""
Settings used to inform a fetcher what to fetch
"""

from dataclasses import dataclass, field
import typing as t


@dataclass
class CollaborationConfigBase:
    """
    Settings used to inform a fetcher what to fetch.

    Extend with any additional fields that you need to inform your API how
    and what to fetch.

    Management of persisting these is left to the specific platform
    (i.e. CLI or HMA).
    """

    name: str
    api: str  # Often a const for implementations
    # Whether to fetch/sync. Some implementations (like the CLI) may gate matching
    # to avoid waiting for an index rebuild to stop processing matches
    enabled: bool
    # Only fetch and index these types
    only_signal_types: t.Set[str]
    # Don't fetch and index these types
    not_signal_types: t.Set[str]
    # Only use signals from these owners
    only_owners: t.Set[int]
    not_owners: t.Set[int]
    # Only use signals with these tags
    only_tags: t.Set[str]
    not_tags: t.Set[str]


@dataclass
class DefaultsForCollabConfigBase:
    enabled: bool = True
    only_signal_types: t.Set[str] = field(default_factory=set)
    not_signal_types: t.Set[str] = field(default_factory=set)
    only_owners: t.Set[int] = field(default_factory=set)
    not_owners: t.Set[int] = field(default_factory=set)
    only_tags: t.Set[str] = field(default_factory=set)
    not_tags: t.Set[str] = field(default_factory=set)


@dataclass
class SimpleCollabConfig(CollaborationConfigBase, DefaultsForCollabConfigBase):
    """Fill out all the defaults in an MRO-friendly way"""


class CollaborationConfigStoreBase:
    def get_all_collabs(self) -> t.List[CollaborationConfigBase]:
        """
        Get all CollaborationConfigs, already resolved to the correct type
        """
        raise NotImplementedError

    def get_collab(self, name: str) -> t.Optional[CollaborationConfigBase]:
        """Get a specific collab config by name"""
        return next((c for c in self.get_all_collabs() if c.name == name), None)
