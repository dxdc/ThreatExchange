# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

"""
Local storage and configuration for the CLI.

The CLI and Hasher-Matcher-Actioner are roughly parallel, but this isn't a 
scalable service running on AWS. Instead, we have all of our state in
a file (likely ~/.threatexchange)
"""

from dataclasses import dataclass, field
import sys
import typing as t
import json
import pathlib
import logging

from dacite import WrongTypeError

from threatexchange.fetcher import collab_config
from threatexchange.fetcher.fetch_api import SignalExchangeAPI
from threatexchange.content_type import content_base
from threatexchange.fetcher.fetch_state import FetchedStateStoreBase
from threatexchange.fetcher.apis.static_sample import StaticSampleSignalExchangeAPI
from threatexchange.signal_type import signal_base
from threatexchange.meta import FunctionalityMapping
from threatexchange.cli.cli_state import CliSimpleState, CliIndexStore
from threatexchange.cli import dataclass_json as cli_json


CONFIG_FILENAME = "config.json"


@dataclass
class CLiConfig:
    """A place to store misc configuration for the CLI"""

    fb_threatexchange_api_token: t.Optional[str] = None
    extensions: t.Set[str] = field(default_factory=set)


class CliState(collab_config.CollaborationConfigStoreBase):
    """
    A wrapper around stateful information stored for the CLI.

    Everything is just in a single file (usually ~/.threatexchange).
    """

    def __init__(
        self, fetch_types: t.List[t.Union[SignalExchangeAPI, t.Type[SignalExchangeAPI]]]
    ):
        dir = pathlib.Path("~/.threatexchange/").expanduser()
        self._dir = dir

        self._name_to_ctype = {
            ft.get_name(): ft.get_config_class() for ft in fetch_types
        }

        self._cache: t.Optional[
            t.Dict[str, collab_config.CollaborationConfigBase]
        ] = None

        self._init_folders_if_needed()

    def _init_folders_if_needed(self):
        for d in (self.collab_dir, self.index_dir, self.fetched_state_dir):
            if not d.is_dir():
                d.mkdir(parents=True)
        cfg = self.config_file
        if not cfg.is_file():
            cfg.write_text("{}")

    @property
    def collab_dir(self) -> pathlib.Path:
        return self._dir / "collab_configs/"

    @property
    def fetched_state_dir(self) -> pathlib.Path:
        return self._dir / "fetched"

    @property
    def index_dir(self) -> pathlib.Path:
        return self._dir / "index/"

    @property
    def config_file(self) -> pathlib.Path:
        return self._dir / "config.json"

    def path_for_collab_config(
        self, config: collab_config.CollaborationConfigBase
    ) -> pathlib.Path:
        return self.collab_dir / f"{config.name}.json"

    def get_persistent_config(self) -> CLiConfig:
        return cli_json.dataclass_load_file(
            self.config_file, CLiConfig, default=CLiConfig()
        )

    def update_persistent_config(self, config: CLiConfig):
        cli_json.dataclass_dump_file(self.config_file, config)

    def dir_for_fetched_state(
        self,
        api: t.Type[SignalExchangeAPI],
    ) -> pathlib.Path:
        return self.fetched_state_dir / f"{api.get_name()}/"

    def get_collab_names_without_loading(self) -> t.List[str]:
        if self._cache is not None:
            return list(self._cache)
        return [str(p) for p in self.collab_dir.glob("*.json")]

    def get_all_collabs(self) -> t.List[collab_config.CollaborationConfigBase]:
        """
        Get all CollaborationConfigs, already resolved to the correct type
        """
        if self._cache is None:
            collab_dir = self.collab_dir

            ret = []
            for f in collab_dir.glob("*.json"):
                if not f.is_file():
                    logging.warning("Ignoring strange file in collab dir: %s", f)
                    continue
                with f.open() as fp:
                    try:
                        content = json.load(fp)
                    except json.JSONDecodeError:
                        logging.exception("Failed to parse collab config: %s", f)
                        continue
                    ctype = None
                    if isinstance(content, dict):
                        ctype = self._name_to_ctype.get(content.get("api"))  # type: ignore
                    if ctype is None:
                        logging.warning("Ignoring collab config of unknown type: %s", f)
                        continue
                try:
                    config = cli_json.dataclass_load_dict(content, ctype)
                    ret.append(config)
                except WrongTypeError:
                    logging.exception("Failed to parse collab config: %s", f)
            self._cache = {c.name: c for c in ret}
        return list(self._cache.values())

    def update_collab(self, collab: collab_config.CollaborationConfigBase) -> None:
        """Create or update a collaboration"""
        path = self.path_for_collab_config(collab)
        cli_json.dataclass_dump_file(path, collab)

    def delete_collab(self, collab: collab_config.CollaborationConfigBase) -> None:
        """Delete a collaboration"""
        self.path_for_collab_config(collab).unlink(missing_ok=True)


class CLISettings:
    """
    A God object for all miscellanious persisted state to make the CLI work
    """

    def __init__(
        self,
        mapping: FunctionalityMapping,
        cli_state: CliState,
    ) -> None:
        self._mapping = mapping
        self._state = cli_state
        self._sample_message_printed = False
        self._config: t.Optional[CLiConfig] = None
        self.index_store = CliIndexStore(cli_state.index_dir)

    def get_persistent_config(self) -> CLiConfig:
        if self._config is None:
            self._config = self._state.get_persistent_config()
        return self._config

    def set_persistent_config(self, config: CLiConfig) -> None:
        self._state.update_persistent_config(config)
        self._config = config

    def get_all_content_types(self) -> t.List[t.Type[content_base.ContentType]]:
        return list(self._mapping.signal_and_content.content_by_name.values())

    def get_content_type(self, name: str) -> t.Type[content_base.ContentType]:
        return self._mapping.signal_and_content.content_by_name[name]

    def get_all_signal_types(self) -> t.List[t.Type[signal_base.SignalType]]:
        return list(self._mapping.signal_and_content.signal_type_by_name.values())

    def get_signal_type(self, name: str) -> t.Type[signal_base.SignalType]:
        return self._mapping.signal_and_content.signal_type_by_name[name]

    def get_signal_types_for_content(
        self, content_type: t.Type[content_base.ContentType]
    ) -> t.List[t.Type[signal_base.SignalType]]:
        return self._mapping.signal_and_content.signal_type_by_content[content_type]

    def get_fetchers(self):
        return [fs for fs in self._mapping.fetcher.fetchers_by_name.values()]

    def get_api_for_collab(
        self, collab: collab_config.CollaborationConfigBase
    ) -> SignalExchangeAPI:
        return self._mapping.fetcher.fetchers_by_name[collab.api]

    def get_fetch_store_for_fetcher(
        self, fetcher: t.Type[SignalExchangeAPI]
    ) -> FetchedStateStoreBase:
        return CliSimpleState(fetcher, self._state.dir_for_fetched_state(fetcher))

    def get_fetch_store_for_collab(
        self, collab: collab_config.CollaborationConfigBase
    ) -> FetchedStateStoreBase:
        return self.get_fetch_store_for_fetcher(
            self._mapping.fetcher.fetchers_by_name[collab.api].__class__
        )

    def get_all_collabs(
        self, *, default_to_sample: bool = False
    ) -> t.List[collab_config.CollaborationConfigBase]:
        collabs = self._state.get_all_collabs()
        if not collabs and default_to_sample:
            return [self._get_sample_collab()]
        # Should this check whether the APIs are all valid?
        return collabs

    def get_collab(
        self,
        name: str,
    ) -> t.Optional[collab_config.CollaborationConfigBase]:
        return self._state.get_collab(name)

    def _get_sample_collab(self) -> collab_config.CollaborationConfigBase:
        if not self._sample_message_printed:
            print(
                (
                    "Looks like you haven't set up a collaboration config, "
                    "so using the sample one against sample data"
                ),
                file=sys.stderr,
            )
            self._sample_message_printed = True
        return collab_config.CollaborationConfigBase(
            "Sample Signals",
            StaticSampleSignalExchangeAPI.get_name(),
            enabled=True,
            only_signal_types={s.get_name() for s in self.get_all_signal_types()},
            not_signal_types=set(),
            only_owners=set(),
            not_owners=set(),
            only_tags=set(),
            not_tags=set(),
        )

    def get_collabs_for_fetcher(
        self, fetcher: SignalExchangeAPI
    ) -> t.List[collab_config.CollaborationConfigBase]:
        api_name = fetcher.get_name()
        return [c for c in self.get_all_collabs() if c.api == api_name]
